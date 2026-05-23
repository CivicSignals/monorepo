"""Deterministic fixture-replaying completion backend for the e2e stack.

Unlike :class:`~civicsignals_api.llm_gateway.backends.fake.FakeBackend` — whose
scripted ``responses`` are consumed FIFO and so depend on call *order* — the
:class:`FixtureBackend` keys its scripted replies on **what** is being asked:

- which **scenario** is being processed (a unique marker token embedded in the
  document text — every e2e fixture's ``source.html`` carries one), and
- which **call kind** the gateway issued — relevance gate (``classify`` task,
  doc 19 §3), Stage-3 entity extraction (``entity_extraction`` prompt, doc 19 §4),
  or Stage-4 signal extraction (``signal_extraction`` prompt, doc 19 §5).

The backend only sees the *rendered* prompt + system text (the gateway resolves
the prompt registry before calling :meth:`complete`), so the call kind is detected
from stable phrases in those rendered templates rather than a prompt name. This
makes a running api/worker boot with ``LLM_BACKEND=fake`` produce the exact signals
the fixtures intend, with **no network and no API key** — the foundation the e2e
seed + tests replay against (doc 06 §7: nothing bypasses the gateway).

The script is a plain dict (loaded from the manifest's per-scenario
``llm-responses.json`` files, merged by ``seed_e2e``):

    {
      "<scenario marker>": {
        "relevance":  {... or a JSON string ...},
        "entity":     {...},
        "signal":     {...}
      },
      ...
    }

A response value may be a JSON-serialisable object (it is ``json.dumps``-ed) or an
already-serialised string (used verbatim). Missing entries fall back to safe
defaults: an unknown scenario or a relevance call with no scripted verdict yields a
permissive "relevant" verdict; an unscripted entity call yields an empty extraction;
an unscripted signal call yields ``{"candidates": []}``. Nothing raises — the goal
is determinism, not strictness.
"""

from __future__ import annotations

import json
from typing import Final

from ..types import LLMResult

# Stable discriminators present in the *rendered* prompt of each call kind. They are
# load-bearing: the relevance gate inlines its prompt (relevance.py ``_PROMPT_TEMPLATE``),
# while the two extraction passes render the on-disk prompts
# (``prompts/entity_extraction/v2.md`` + ``prompts/signal_extraction/v1.md``).
_RELEVANCE_MARKER: Final = "Does this document mention or discuss"
_ENTITY_MARKER: Final = "Extract all named entities from the document below"
_SIGNAL_MARKER: Final = "extract every buying signal it contains"

# Script keys per call kind.
KIND_RELEVANCE: Final = "relevance"
KIND_ENTITY: Final = "entity"
KIND_SIGNAL: Final = "signal"

# Safe fallbacks when a scenario / call kind has no scripted response.
_DEFAULT_RELEVANCE: Final = {"relevant": True, "categories": [], "confidence": 0.9}
_DEFAULT_ENTITY: Final[dict[str, object]] = {
    "organizations": [],
    "persons": [],
    "monetary_amounts": [],
    "dates": [],
    "products_categories": [],
    "vendors_mentioned": [],
    "contract_terms_mentions": [],
    "raw_keywords": [],
    "extraction_confidence": 0.0,
    "extraction_warnings": [],
}
_DEFAULT_SIGNAL: Final[dict[str, object]] = {"candidates": []}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token), matching :class:`FakeBackend`."""
    return max(1, len(text) // 4)


def detect_call_kind(prompt: str) -> str:
    """Classify a rendered prompt as a relevance / entity / signal call (doc 19)."""
    if _RELEVANCE_MARKER in prompt:
        return KIND_RELEVANCE
    if _ENTITY_MARKER in prompt:
        return KIND_ENTITY
    if _SIGNAL_MARKER in prompt:
        return KIND_SIGNAL
    # An unrecognised completion (e.g. a summary / smart-search call the e2e seed
    # never drives) is treated as a signal call so it returns an empty-candidates
    # JSON rather than echoing an unbounded prompt back into the pipeline.
    return KIND_SIGNAL


class FixtureBackend:
    """A deterministic backend that replays scripted per-scenario / per-task JSON.

    ``script`` maps a scenario marker → ``{relevance|entity|signal: response}``.
    The backend finds the scenario whose marker appears in the rendered prompt and
    returns that scenario's response for the detected call kind. It records every
    call on :attr:`calls` (like :class:`FakeBackend`) so tests can assert routing.
    """

    provider = "fake"

    def __init__(
        self,
        *,
        script: dict[str, dict[str, object]] | None = None,
        provider: str = "fake",
    ) -> None:
        self.provider = provider
        self._script: dict[str, dict[str, object]] = dict(script or {})
        self.calls: list[dict[str, object]] = []

    def _scenario_for(self, prompt: str) -> dict[str, object] | None:
        """Return the script entry whose marker is present in ``prompt`` (or None)."""
        for marker, responses in self._script.items():
            if marker in prompt:
                return responses
        return None

    def _response_text(self, prompt: str, kind: str) -> str:
        scenario = self._scenario_for(prompt)
        default = {
            KIND_RELEVANCE: _DEFAULT_RELEVANCE,
            KIND_ENTITY: _DEFAULT_ENTITY,
            KIND_SIGNAL: _DEFAULT_SIGNAL,
        }[kind]
        value: object = default
        if scenario is not None and kind in scenario:
            value = scenario[kind]
        if isinstance(value, str):
            # An already-serialised reply: pass through verbatim.
            return value
        return json.dumps(value)

    async def complete(
        self,
        *,
        prompt: str,
        model: str,
        max_tokens: int,
        temperature: float = 0.0,
        system: str | None = None,
    ) -> LLMResult:
        kind = detect_call_kind(prompt)
        text = self._response_text(prompt, kind)
        self.calls.append(
            {
                "prompt": prompt,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "kind": kind,
            }
        )
        return LLMResult(
            text=text,
            model=model,
            provider=self.provider,
            input_tokens=_estimate_tokens(prompt + (system or "")),
            output_tokens=_estimate_tokens(text),
        )
