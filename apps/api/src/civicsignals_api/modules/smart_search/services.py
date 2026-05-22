"""Public service interface for the smart_search module.

Other modules call smart_search only through the functions defined here — never by
importing smart_search's models or routes directly (doc 06 §3).

:class:`QueryRewriter` is the NL -> structured query rewrite (TODO I2). It runs a
small, cheap LLM call (via the gateway's ``smart_search_rewrite`` task policy) that
turns a natural-language search box query into a validated :class:`SearchFilters`
set plus a residual free-text query for hybrid retrieval (TODO I3). The LLM output
is schema-constrained: we instruct strict JSON, parse + validate it, retry once on
invalid output, and on persistent failure fall back to treating the whole input as
the text query (``degraded=True``) so the search box never hard-fails.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, get_args

import structlog
from pydantic import ValidationError

from civicsignals_api.llm_gateway import (
    TASK_SMART_SEARCH_REWRITE,
    LLMError,
    LLMGateway,
    LLMResult,
    get_gateway,
)

from .schemas import (
    EntityKind,
    SearchFilters,
    SignalStatus,
    SignalType,
    StructuredQuery,
)

log = structlog.get_logger(__name__)

# Prompt-registry seam (TODO E3): once the registry lands, this name+version
# resolves to the versioned text in ``apps/api/prompts/`` instead of the inline
# default below, with no change to this call site.
REWRITE_PROMPT_NAME = "smart_search_rewrite"
REWRITE_PROMPT_VERSION = "v1"

# Allowed enum values, sourced from the schema Literals so the prompt and the
# repair pass never drift from the validated filter fields (doc 08 §3.2).
_SIGNAL_TYPES: tuple[str, ...] = get_args(SignalType)
_ENTITY_KINDS: tuple[str, ...] = get_args(EntityKind)
_STATUSES: tuple[str, ...] = get_args(SignalStatus)

# TODO E3: inline default prompt. Move to apps/api/prompts/smart_search_rewrite/v1.txt
# once the prompt registry exists; reference it via REWRITE_PROMPT_NAME/_VERSION.
_SYSTEM_PROMPT = f"""\
You convert a user's natural-language search query for U.S. government "signals"
(RFPs, budgets, grants, board decisions, etc.) into a structured JSON object.

Return ONLY a single JSON object, no markdown fences and no prose. The object must
match this shape exactly (omit any field you cannot confidently fill):

{{
  "text": string,                 // residual free-text for full-text/semantic search
  "keywords": [string],           // salient terms (optional)
  "entities": [string],           // named org/place hints, e.g. "Northshore School District"
  "filters": {{
    "signal_type": [string],      // one or more of: {", ".join(_SIGNAL_TYPES)}
    "entity_kind": [string],      // one or more of: {", ".join(_ENTITY_KINDS)}
    "state": [string],            // two-letter US state codes, e.g. "WA"
    "status": [string],           // one or more of: {", ".join(_STATUSES)}
    "min_score": number,          // 0..1 relevance floor, only if the query implies it
    "published_at_gte": string,   // ISO date (YYYY-MM-DD), inclusive lower bound
    "published_at_lt": string     // ISO date (YYYY-MM-DD), exclusive upper bound
  }}
}}

Rules:
- Only use the enum values listed above; never invent new ones.
- Put words that are NOT captured by a filter into "text".
- Use "entities" for named organizations/places; do NOT guess IDs.
- If nothing maps to a filter, return all of the user's query as "text".
"""


class QueryRewriter:
    """Rewrites a natural-language search query into a :class:`StructuredQuery`.

    Routes a small/cheap completion through the LLM gateway
    (``task=smart_search_rewrite``). The structured output is strictly validated;
    invalid LLM output is retried once and then degrades to a plain text query.
    """

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self._gateway = gateway or get_gateway()

    async def rewrite(
        self,
        query: str,
        *,
        workspace_id: str | None = None,
        available_signal_types: Sequence[str] | None = None,
        available_states: Sequence[str] | None = None,
    ) -> tuple[StructuredQuery, LLMResult | None]:
        """Rewrite ``query`` into a structured query.

        ``available_signal_types``/``available_states`` are optional workspace
        context (the types/geographies actually present in the workspace) that
        steer the model; they do not change the strict output schema. Returns the
        :class:`StructuredQuery` plus the gateway :class:`LLMResult` (for token
        usage/provenance), or ``None`` for the result when we never reached the
        model (empty query) or the call failed and we fell back.
        """
        text = query.strip()
        if not text:
            return StructuredQuery(text="", degraded=True), None

        prompt = self._build_prompt(
            text,
            available_signal_types=available_signal_types,
            available_states=available_states,
        )

        # One call + one repair retry: a transient/transport failure inside the
        # gateway is already retried with backoff there; here we retry the *parse*
        # of a malformed-but-returned body once before degrading.
        last_result: LLMResult | None = None
        for attempt in range(2):
            try:
                result = await self._gateway.complete(
                    prompt=prompt,
                    task=TASK_SMART_SEARCH_REWRITE,
                    system=_SYSTEM_PROMPT,
                    workspace_id=workspace_id,
                    max_tokens=512,
                    temperature=0.0,
                    prompt_name=REWRITE_PROMPT_NAME,
                    prompt_version=REWRITE_PROMPT_VERSION,
                )
            except LLMError:
                log.warning("smart_search.rewrite.llm_error", attempt=attempt, exc_info=True)
                break
            last_result = result
            parsed = self._parse(result.text, original=text)
            if parsed is not None:
                return parsed, result
            log.info("smart_search.rewrite.invalid_output", attempt=attempt)

        # Fallback: treat the whole input as the text query (doc 08 §3.2 `q`).
        return StructuredQuery(text=text, degraded=True), last_result

    def _build_prompt(
        self,
        query: str,
        *,
        available_signal_types: Sequence[str] | None,
        available_states: Sequence[str] | None,
    ) -> str:
        lines = [f"User query: {query}"]
        if available_signal_types:
            allowed = [t for t in available_signal_types if t in _SIGNAL_TYPES]
            if allowed:
                lines.append(f"Signal types present in this workspace: {', '.join(allowed)}")
        if available_states:
            states = sorted({s.strip().upper() for s in available_states if s.strip()})
            if states:
                lines.append(f"Geographies present in this workspace: {', '.join(states)}")
        return "\n".join(lines)

    def _parse(self, raw: str, *, original: str) -> StructuredQuery | None:
        """Parse + repair + validate one LLM body. ``None`` means unrecoverable."""
        payload = self._extract_json_object(raw)
        if payload is None:
            return None
        repaired = self._repair(payload, original=original)
        try:
            return StructuredQuery.model_validate(repaired)
        except ValidationError:
            log.info("smart_search.rewrite.validation_failed", exc_info=True)
            return None

    @staticmethod
    def _extract_json_object(raw: str) -> dict[str, Any] | None:
        """Pull the first JSON object out of a model body.

        Tolerates surrounding prose or ```` ```json ```` fences by slicing from the
        first ``{`` to the last ``}`` before parsing.
        """
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            return None
        try:
            obj = json.loads(raw[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            return None
        return obj if isinstance(obj, dict) else None

    @staticmethod
    def _repair(payload: dict[str, Any], *, original: str) -> dict[str, Any]:
        """Best-effort repair before strict validation.

        Drops out-of-enum filter values (a hallucinated ``signal_type`` is dropped
        rather than failing the whole rewrite) and guarantees a non-empty ``text``
        so an all-filters rewrite still has something for full-text search to fall
        back on. We never *add* fields the model omitted beyond ``text``.
        """
        raw_filters = payload.get("filters")
        filters: dict[str, Any] = dict(raw_filters) if isinstance(raw_filters, dict) else {}

        def _clean_enum(key: str, allowed: tuple[str, ...]) -> None:
            value = filters.get(key)
            if isinstance(value, str):
                value = [value]
            if isinstance(value, list):
                kept = [v for v in value if v in allowed]
                if kept:
                    filters[key] = kept
                else:
                    filters.pop(key, None)
            elif value is not None:
                filters.pop(key, None)

        _clean_enum("signal_type", _SIGNAL_TYPES)
        _clean_enum("entity_kind", _ENTITY_KINDS)
        _clean_enum("status", _STATUSES)

        repaired: dict[str, Any] = dict(payload)
        repaired["filters"] = filters
        text = repaired.get("text")
        if not isinstance(text, str) or not text.strip():
            repaired["text"] = original
        return repaired


__all__ = ["QueryRewriter", "SearchFilters", "StructuredQuery"]
