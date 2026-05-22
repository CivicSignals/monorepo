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

import base64
import binascii
import datetime as dt
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, get_args

import structlog
from pydantic import ValidationError
from sqlalchemy import bindparam, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.llm_gateway import (
    TASK_SMART_SEARCH_REWRITE,
    TASK_SMART_SEARCH_SUMMARY,
    LLMError,
    LLMGateway,
    LLMResult,
    get_gateway,
)
from civicsignals_api.modules.signals.models import EMBEDDING_DIM, Signal
from civicsignals_api.modules.signals.schemas import SignalRead

from .schemas import (
    EntityKind,
    FusionWeights,
    SearchFilters,
    SignalStatus,
    SignalType,
    SmartSearchResponse,
    SmartSearchResult,
    StructuredQuery,
)

log = structlog.get_logger(__name__)

# Prompt-registry seam (TODO E3): once the registry lands, this name+version
# resolves to the versioned text in ``apps/api/prompts/`` instead of the inline
# default below, with no change to this call site.
REWRITE_PROMPT_NAME = "smart_search_rewrite"
REWRITE_PROMPT_VERSION = "v1"

# Summarization (I4) — prompt registry seam (TODO E3): same approach as the rewrite.
# The name ``smart_search_summary`` resolves to the versioned prompt once E3 lands.
SUMMARY_PROMPT_NAME = "smart_search_summary"
SUMMARY_PROMPT_VERSION = "v1"

# Cap the number of results fed to the summarizer to bound the prompt size and
# per-query LLM token cost (I4). The summarizer always operates on the first page
# of results regardless of ``top_n``; a caller that requests page 2+ via ``cursor``
# gets no summary (only first-page calls make sense to summarize).
SUMMARY_TOP_N = 5
# Max characters of title + summary included per signal in the summary prompt.
# Truncation avoids runaway prompts for signals with very long summaries.
_SUMMARY_SIGNAL_CHARS = 400

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

# TODO E3: inline default summary prompt. Move to
# apps/api/prompts/smart_search_summary/v1.md once the prompt registry exists;
# reference it via SUMMARY_PROMPT_NAME/SUMMARY_PROMPT_VERSION.
_SUMMARY_SYSTEM_PROMPT = """\
You synthesize the top results from a CivicSignals smart search into a concise
natural-language paragraph for a sales professional. You write in active, specific
prose — not bullet points. You do not invent facts beyond what the results contain.
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
        """Pull the first balanced JSON object out of a model body.

        Tolerates surrounding prose or ```` ```json ```` fences. Scans from the
        first ``{`` and tracks brace depth (ignoring braces inside string
        literals) to find the matching ``}``, so trailing braces in prose or a
        second object after the first do not break parsing.
        """
        start = raw.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escaped = False
        candidate: str | None = None
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[start : i + 1]
                    break
        if candidate is None:
            return None
        try:
            obj = json.loads(candidate)
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


# ===========================================================================
# Hybrid retrieval (TODO I3, doc 14 §6.2, doc 19 §7.4)
# ===========================================================================
#
# Given a NL query we:
#   1. rewrite it (I2) -> {structured filters, residual text query, keywords};
#   2. run three retrievers over the *global* signal corpus, scoped to the
#      structured filters as a hard intersection:
#        (a) vector ANN  — embed the text via the gateway, cosine ANN over
#            ``signals_signal.vector_embedding`` (I1's ivfflat index);
#        (b) BM25 / FTS  — Postgres ``websearch_to_tsquery`` over the GIN-indexed
#            ``to_tsvector(title || ' ' || summary)`` (this task's migration);
#        (c) structured filter — entity/type/date/state from the rewrite.
#   3. fuse the vector + BM25 candidate rankings with weighted reciprocal-rank
#      fusion (RRF), intersected with the structured-filter set, and return a
#      ranked, cursor-paginated page.
#
# The signal corpus is global (doc 14 §4.2); the search runs in a *workspace
# context* (the route is behind ``require_workspace``) so per-workspace scoring
# (F3) can boost the ranking later (the ``# TODO F3`` hook in ``_fuse``).


# A page of pure-id fusion output is offset-paginated over a deterministic ranking,
# so the cursor is just an opaque base64-encoded integer offset (doc 06 §5: opaque
# cursor, never a raw offset in the URL).
def _encode_offset_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode("ascii")).decode("ascii")


def _decode_offset_cursor(cursor: str) -> int:
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("ascii"))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid cursor") from exc
    if offset < 0:
        raise ValueError("invalid cursor")
    return offset


@dataclass(frozen=True)
class FusedHit:
    """One signal id with its fused score + per-retriever provenance.

    Pure data — the deterministic output of :func:`fuse_rankings`, independent of
    the DB. ``vector_rank`` / ``bm25_rank`` are 0-based ranks in each retriever's
    list (``None`` = absent from that list). ``matched_via`` is the ordered set of
    retrievers that surfaced this id (for the "why this result?" panel).
    """

    signal_id: uuid.UUID
    score: float
    matched_via: tuple[str, ...]
    vector_rank: int | None
    bm25_rank: int | None


def fuse_rankings(
    *,
    vector_ids: Sequence[uuid.UUID],
    bm25_ids: Sequence[uuid.UUID],
    filter_ids: set[uuid.UUID] | None,
    weights: FusionWeights,
) -> list[FusedHit]:
    """Weighted reciprocal-rank fusion of the vector + BM25 rankings (doc 14 §6.2).

    Pure + deterministic so the ranking is unit-testable without a DB. Each list is
    a ranking (most-relevant first); a signal at 0-based rank ``r`` contributes
    ``weight / (rrf_k + r + 1)`` from that retriever. The two contributions sum to
    the fused score. ``filter_ids`` is the structured-filter intersection: when not
    ``None``, the fused set is restricted to it (a hard gate — a candidate that the
    vector/BM25 retrievers surfaced but that fails the filters is dropped). When
    ``None``, no structured filter was applied (the rewrite extracted none) and the
    full vector-union-BM25 candidate set ranks.

    Ties (equal fused score) break deterministically by signal id so pagination is
    stable across calls. ``matched_via`` records which retrievers (and the filter)
    contributed, surfaced to the UI.

    # TODO F3: fold a per-workspace ``signals_workspace_score`` term into the fused
    # score here (a third weighted contribution) once F3 lands — the workspace is
    # already in scope (the route requires it).
    """
    k = weights.rrf_k

    vec_rank = {sid: r for r, sid in enumerate(vector_ids)}
    bm_rank = {sid: r for r, sid in enumerate(bm25_ids)}

    retrieved: set[uuid.UUID] = set(vec_rank) | set(bm_rank)
    if filter_ids is None:
        # No structured filter: rank the full vector-union-BM25 candidate set.
        candidates = retrieved
    elif retrieved:
        # Structured filter is a hard gate over the text retrievers' candidates.
        candidates = retrieved & filter_ids
    else:
        # Filter-only query (no residual text → no vector/BM25 candidates): the
        # filter set *is* the result so a pure structured query still returns rows.
        candidates = set(filter_ids)

    hits: list[FusedHit] = []
    for sid in candidates:
        score = 0.0
        via: list[str] = []
        vr = vec_rank.get(sid)
        if vr is not None and weights.vector > 0.0:
            score += weights.vector / (k + vr + 1)
            via.append("vector")
        br = bm_rank.get(sid)
        if br is not None and weights.bm25 > 0.0:
            score += weights.bm25 / (k + br + 1)
            via.append("bm25")
        if filter_ids is not None:
            via.append("filter")
        # A candidate may be in the filter set only (no vector/bm25 hit) when the
        # query was filter-only; it still ranks (score 0 from fusion) so a pure
        # structured query returns results.
        hits.append(
            FusedHit(
                signal_id=sid,
                score=score,
                matched_via=tuple(via),
                vector_rank=vr,
                bm25_rank=br,
            )
        )

    # Highest score first; stable, deterministic tiebreak on the (time-ordered) id.
    hits.sort(key=lambda h: (-h.score, h.signal_id.bytes))
    return hits


class ResultSummarizer:
    """LLM synthesis of the top-N hybrid-retrieval results (I4).

    Given the original NL ``query`` and a list of :class:`SmartSearchResult` objects,
    produces a short natural-language paragraph that synthesises the most relevant
    results — e.g. "Here are 3 RFPs matching your search for cybersecurity services.
    The most relevant is … from …, due …".

    The summarizer is **optional** and **non-fatal**: if the LLM call fails, or if
    there are no results, it returns ``None`` so the caller can omit the field from
    the response without breaking the search.

    Token cost is bounded by:
    - capping the number of results fed into the prompt at :data:`SUMMARY_TOP_N`;
    - truncating each result's title + summary text to :data:`_SUMMARY_SIGNAL_CHARS`
      characters so a result with a very long summary does not blow up the prompt.

    Token usage is metered against ``workspace_id`` via the gateway accountant
    (doc 06 §7); the ``TASK_SMART_SEARCH_SUMMARY`` task routes to a cheap Haiku-class
    model (I4 cost note: Haiku keeps the per-call cost at ~$0.0001-0.0005).
    """

    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self._gateway = gateway or get_gateway()

    async def summarize(
        self,
        query: str,
        results: Sequence[SmartSearchResult],
        *,
        workspace_id: str | None = None,
    ) -> str | None:
        """Synthesize ``results`` (top-N) into a short paragraph.

        Returns ``None`` when there are no results or on any LLM failure (graceful
        degradation — callers must not depend on a summary being present).
        """
        if not results:
            return None

        top = list(results[:SUMMARY_TOP_N])
        prompt = self._build_prompt(query, top)
        try:
            # E2 seam: ``prompt=`` is set, so the gateway uses the raw inline
            # text and treats ``prompt_name``/``prompt_version`` as provenance
            # metadata only (no registry lookup). Once E3 lands, remove
            # ``prompt=`` and ``system=`` here; the registry will resolve
            # ``SUMMARY_PROMPT_NAME`` to ``apps/api/prompts/
            # smart_search_summary/v1.md`` and render both body + system.
            result = await self._gateway.complete(
                prompt=prompt,
                task=TASK_SMART_SEARCH_SUMMARY,
                system=_SUMMARY_SYSTEM_PROMPT,
                workspace_id=workspace_id,
                max_tokens=256,
                temperature=0.3,
                prompt_name=SUMMARY_PROMPT_NAME,
                prompt_version=SUMMARY_PROMPT_VERSION,
            )
        except LLMError:
            log.warning("smart_search.summarize.llm_error", exc_info=True)
            return None

        text = result.text.strip()
        return text or None

    @staticmethod
    def _build_prompt(query: str, results: list[SmartSearchResult]) -> str:
        """Build the user-turn prompt for the summarizer.

        Short and token-efficient: the query, the count, and one truncated
        "title — entity — summary" line per result. The model fills in prose.
        """
        lines = [
            f"Search query: {query}",
            f"Number of results: {len(results)}",
            "",
            "Top results (title | entity | excerpt):",
        ]
        for i, r in enumerate(results, 1):
            sig = r.signal
            title = (sig.title or "").strip()
            summary = (sig.summary or "").strip()
            entity = (sig.entity_name_raw or "").strip()
            # Truncate the combined text to bound the prompt size.
            snippet = f"{title} — {entity} — {summary}"
            if len(snippet) > _SUMMARY_SIGNAL_CHARS:
                snippet = snippet[: _SUMMARY_SIGNAL_CHARS - 1] + "…"
            lines.append(f"{i}. {snippet}")

        lines.extend(
            [
                "",
                "Write a concise 1-3 sentence synthesis of these results for a "
                "sales professional. Be specific: name the most relevant result, "
                "the issuing entity, and any key details (due date, dollar amount). "
                "Do not invent facts beyond what is listed above.",
            ]
        )
        return "\n".join(lines)


class HybridRetriever:
    """Hybrid retrieval over the global signal corpus (TODO I3, doc 14 §6.2).

    Orchestrates rewrite -> {vector ANN, BM25 FTS, structured filter} -> fusion.
    Holds the gateway (for embedding the query text) + a :class:`QueryRewriter`;
    each ``search`` call takes a DB session so the same retriever instance serves
    many requests.
    """

    def __init__(
        self,
        gateway: LLMGateway | None = None,
        *,
        rewriter: QueryRewriter | None = None,
        summarizer: ResultSummarizer | None = None,
    ) -> None:
        self._gateway = gateway or get_gateway()
        self._rewriter = rewriter or QueryRewriter(self._gateway)
        self._summarizer = summarizer or ResultSummarizer(self._gateway)

    async def search(
        self,
        session: AsyncSession,
        query: str,
        *,
        workspace_id: str | None = None,
        extra_filters: SearchFilters | None = None,
        top_n: int = 25,
        candidate_limit: int = 100,
        weights: FusionWeights | None = None,
        cursor: str | None = None,
        summarize: bool = False,
    ) -> SmartSearchResponse:
        """Run hybrid retrieval for a natural-language ``query`` (doc 14 §6.2).

        1. Rewrite the NL query (I2) into structured filters + a residual text query.
        2. Merge ``extra_filters`` (explicit UI filter chips) over the rewrite's.
        3. Run vector ANN + BM25 FTS (both scoped to the merged filters) and a
           structured-filter id query, then fuse.
        4. Hydrate the top-N (after the cursor offset) into :class:`SignalRead`.
        5. Optionally summarize the top-N results into a short NL paragraph (I4).
           Only on ``summarize=True`` and the first page (``cursor`` is ``None``);
           paginated follow-ups are never summarized. Failures are non-fatal.
           # TODO I5: check per-workspace daily smart-search LLM budget here before
           # calling the summarizer (I5 caps spend per workspace per day; F3 must
           # land first to provide workspace budget data).

        ``workspace_id`` scopes token accounting for the rewrite + query embed
        (doc 06 §7); the signal corpus itself is global (doc 14 §4.2).
        """
        weights = weights or FusionWeights()
        offset = _decode_offset_cursor(cursor) if cursor else 0

        structured, _ = await self._rewriter.rewrite(query, workspace_id=workspace_id)
        filters = _merge_filters(structured.filters, extra_filters)

        # The text fed to the retrievers: the rewrite's residual text (plus surfaced
        # keywords for a bit more BM25 recall). Falls back to the raw query when the
        # rewrite degraded to empty text.
        text = structured.text.strip() or query.strip()
        fts_text = " ".join([text, *structured.keywords]).strip()

        vector_ids = await self._vector_ann(
            session, text=text, filters=filters, limit=candidate_limit, workspace_id=workspace_id
        )
        bm25_ids = await self._bm25(session, text=fts_text, filters=filters, limit=candidate_limit)
        filter_ids = await self._filter_ids(session, filters, limit=candidate_limit)

        fused = fuse_rankings(
            vector_ids=vector_ids,
            bm25_ids=bm25_ids,
            filter_ids=filter_ids,
            weights=weights,
        )

        page = fused[offset : offset + top_n]
        has_more = len(fused) > offset + top_n
        next_cursor = _encode_offset_cursor(offset + top_n) if has_more else None

        results = await self._hydrate(session, page)

        # I4: optional LLM summary. Only on the first page (offset == 0) because a
        # paginated second page is out-of-context for the original NL query's summary,
        # and because callers on page 2+ already have the first-page summary cached.
        summary: str | None = None
        if summarize and offset == 0:
            summary = await self._summarizer.summarize(query, results, workspace_id=workspace_id)

        return SmartSearchResponse(
            results=results,
            next_cursor=next_cursor,
            query=structured,
            degraded=structured.degraded,
            summary=summary,
        )

    # -- retriever (a): vector ANN -------------------------------------------

    async def _vector_ann(
        self,
        session: AsyncSession,
        *,
        text: str,
        filters: SearchFilters,
        limit: int,
        workspace_id: str | None,
    ) -> list[uuid.UUID]:
        """Cosine ANN over ``signals_signal.vector_embedding`` (I1 ivfflat index).

        Embeds the query text via the gateway (best-effort: an embed failure yields
        no vector candidates rather than failing the whole search — BM25 + filters
        still return results, doc 19 §12.1 resilience), then orders by cosine
        distance. NULL-embedding rows are excluded (they cannot ANN-match).
        """
        if not text:
            return []
        try:
            result = await self._gateway.embed([text], workspace_id=workspace_id)
        except LLMError:
            log.warning("smart_search.vector.embed_failed", exc_info=True)
            return []
        if not result.vectors or result.dim != EMBEDDING_DIM:
            log.warning(
                "smart_search.vector.embed_dim_mismatch", got=result.dim, expected=EMBEDDING_DIM
            )
            return []
        query_vec = result.vectors[0]

        stmt = select(Signal.id).where(Signal.vector_embedding.is_not(None))
        stmt = _apply_filters(stmt, filters)
        stmt = stmt.order_by(Signal.vector_embedding.cosine_distance(query_vec)).limit(limit)
        return list((await session.execute(stmt)).scalars().all())

    # -- retriever (b): BM25 / full-text -------------------------------------

    async def _bm25(
        self,
        session: AsyncSession,
        *,
        text: str,
        filters: SearchFilters,
        limit: int,
    ) -> list[uuid.UUID]:
        """Postgres full-text (BM25-style ``ts_rank``) over title+summary (doc 14 §6.2).

        ``websearch_to_tsquery`` parses user-style query text (quoted phrases, ``or``,
        ``-term``) safely. Ordered by ``ts_rank`` desc; only rows that actually match
        the tsquery are returned (the ``@@`` predicate), so this retriever is precise
        (it contributes recall via the union with vector ANN, not noise). Uses the
        same ``to_tsvector(title || ' ' || summary)`` expression the GIN index covers
        so the index is used.
        """
        if not text:
            return []
        # Must match the functional GIN index expression exactly (the I3 migration
        # ``signals_fts_gin_idx``) so the planner uses the index instead of a scan:
        # ``to_tsvector('english', coalesce(title,'') || ' ' || coalesce(summary,''))``.
        tsvector = func.to_tsvector(
            "english",
            func.coalesce(Signal.title, "")
            .op("||")(" ")
            .op("||")(func.coalesce(Signal.summary, "")),
        )
        # websearch_to_tsquery never raises on arbitrary user input (unlike
        # to_tsquery), so untrusted query text is safe to pass directly.
        tsquery = func.websearch_to_tsquery("english", bindparam("q_text", text))
        stmt = select(Signal.id).where(tsvector.op("@@")(tsquery))
        stmt = _apply_filters(stmt, filters)
        stmt = stmt.order_by(func.ts_rank(tsvector, tsquery).desc()).limit(limit)
        return list((await session.execute(stmt)).scalars().all())

    # -- retriever (c): structured filter intersection -----------------------

    async def _filter_ids(
        self,
        session: AsyncSession,
        filters: SearchFilters,
        *,
        limit: int,
    ) -> set[uuid.UUID] | None:
        """The set of signal ids passing the structured filters (the hard gate).

        Returns ``None`` when *no* structured filter was set — that means "do not
        intersect" (the fused set is the full vector-union-BM25 candidates). When at
        least one filter is set, returns the matching id set (capped) so fusion
        intersects with it.

        State filtering is intentionally **not** applied here: ``signals_signal`` has
        no geo column of its own (geo lives on the entity, doc 07 §3); state-scoped
        retrieval is the entity-resolution join that lands with F3/E10. The state
        codes are still echoed back in the response's structured query.
        # TODO E10/F3: join entities_entity for the state/geo filter.

        The gate keys off whether a *currently-enforceable* filter is set
        (signal_type / status / date), **not** ``is_empty()``: a query whose only
        filter is one we cannot yet enforce (state / entity_kind / min_score) must
        return ``None`` (do not intersect) — intersecting with an unordered, capped
        "all rows" set would wrongly drop valid vector/BM25 hits outside the cap.
        """
        if not _has_enforceable_filter(filters):
            return None
        stmt = _apply_filters(select(Signal.id), filters).limit(limit)
        rows = (await session.execute(stmt)).scalars().all()
        return set(rows)

    # -- hydrate fused ids -> read models ------------------------------------

    async def _hydrate(
        self, session: AsyncSession, page: Sequence[FusedHit]
    ) -> list[SmartSearchResult]:
        """Load the page's :class:`Signal` rows and zip them back onto the fused order."""
        if not page:
            return []
        ids = [h.signal_id for h in page]
        rows = (await session.execute(select(Signal).where(Signal.id.in_(ids)))).scalars().all()
        by_id = {r.id: r for r in rows}
        results: list[SmartSearchResult] = []
        for hit in page:
            row = by_id.get(hit.signal_id)
            if row is None:  # pragma: no cover - id came from this same DB
                continue
            results.append(
                SmartSearchResult(
                    signal=SignalRead.model_validate(row),
                    score=hit.score,
                    matched_via=list(hit.matched_via),
                    vector_rank=hit.vector_rank,
                    bm25_rank=hit.bm25_rank,
                )
            )
        return results


def _merge_filters(base: SearchFilters, extra: SearchFilters | None) -> SearchFilters:
    """Merge explicit ``extra`` filter chips over the rewrite's ``base`` filters.

    List fields union (de-duplicated, order-stable); scalar fields (``min_score``,
    the date bounds) take the explicit ``extra`` value when set, else the rewrite's.
    The result is re-validated (so a merged backwards date range is rejected the same
    way the rewrite's would be) — on the rare incoherent merge we fall back to the
    explicit filters alone.
    """
    if extra is None:
        return base

    def _union(a: list[Any], b: list[Any]) -> list[Any]:
        seen: dict[Any, None] = {}
        for v in [*a, *b]:
            seen.setdefault(v, None)
        return list(seen)

    merged = {
        "signal_type": _union(base.signal_type, extra.signal_type),
        "entity_kind": _union(base.entity_kind, extra.entity_kind),
        "state": _union(base.state, extra.state),
        "status": _union(base.status, extra.status),
        "min_score": extra.min_score if extra.min_score is not None else base.min_score,
        "published_at_gte": extra.published_at_gte
        if extra.published_at_gte is not None
        else base.published_at_gte,
        "published_at_lt": extra.published_at_lt
        if extra.published_at_lt is not None
        else base.published_at_lt,
    }
    try:
        return SearchFilters.model_validate(merged)
    except ValidationError:
        log.info("smart_search.merge_filters.incoherent", exc_info=True)
        return extra


def _has_enforceable_filter(filters: SearchFilters) -> bool:
    """Whether ``filters`` constrains a column ``_apply_filters`` actually enforces.

    Only ``signal_type`` / ``status`` / the date range map to ``signals_signal``
    columns today (``_apply_filters``). ``state`` / ``entity_kind`` need the entity
    join and ``min_score`` needs F3, so a filter-set containing *only* those is not
    yet a real intersection — the structured-filter retriever returns ``None`` for it
    (see :meth:`HybridRetriever._filter_ids`).
    """
    return bool(
        filters.signal_type
        or filters.status
        or filters.published_at_gte is not None
        or filters.published_at_lt is not None
    )


def _apply_filters(stmt: Any, filters: SearchFilters) -> Any:
    """Apply the structured filters that map to ``signals_signal`` columns.

    ``signal_type`` / ``status`` are ``IN (...)`` over the row columns; the date
    range is on ``occurred_at`` (the event time, doc 07). ``entity_kind`` / ``state``
    require the entity join (doc 07 §3: geo + kind live on ``entities_entity``), which
    lands with F3/E10 — they are accepted + echoed but not yet applied to the SQL.
    ``min_score`` is the per-workspace relevance floor (F3) — also a no-op here.
    # TODO E10/F3: join entities_entity for entity_kind/state and signals_workspace_score
    # for min_score.
    """
    if filters.signal_type:
        stmt = stmt.where(Signal.signal_type.in_(list(filters.signal_type)))
    if filters.status:
        stmt = stmt.where(Signal.status.in_(list(filters.status)))
    if filters.published_at_gte is not None:
        stmt = stmt.where(
            Signal.occurred_at
            >= dt.datetime.combine(filters.published_at_gte, dt.time.min, tzinfo=dt.UTC)
        )
    if filters.published_at_lt is not None:
        stmt = stmt.where(
            Signal.occurred_at
            < dt.datetime.combine(filters.published_at_lt, dt.time.min, tzinfo=dt.UTC)
        )
    return stmt


__all__ = [
    "SUMMARY_TOP_N",
    "FusedHit",
    "FusionWeights",
    "HybridRetriever",
    "QueryRewriter",
    "ResultSummarizer",
    "SearchFilters",
    "SmartSearchResponse",
    "SmartSearchResult",
    "StructuredQuery",
    "fuse_rankings",
]
