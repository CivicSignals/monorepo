"""Pydantic request/response shapes for the smart_search module (doc 06 §3).

The smart-search rewrite (TODO I2) turns a natural-language search box query into
a *structured* :class:`SearchFilters` set plus a residual free-text query for
full-text/semantic retrieval. The filter fields deliberately mirror the documented
``GET /signals`` feed params (doc 08 §3.2) and the ``POST /saved-searches`` filter
shape (doc 08 §3.3) so hybrid retrieval (TODO I3) and the feed (TODO G1) can consume
the same object without a translation layer. This task only does NL -> structured;
it does not execute the search.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Closed enums copied from the documented feed filters (doc 08 §3.2). The rewrite
# must only emit values from these sets; anything else is dropped during repair so
# a hallucinated ``signal_type`` never reaches the query layer.
SignalType = Literal[
    "rfp_posted",
    "budget_drafted",
    "personnel_change",
    "grant_awarded",
    "board_decision",
    "news_mention",
]
EntityKind = Literal[
    "k12_district",
    "community_college",
    "university",
    "city",
    "county",
    "state_agency",
    "special_district",
]
SignalStatus = Literal["new", "reviewed", "dismissed", "pinned", "pushed"]


class SearchFilters(BaseModel):
    """Structured filters aligned with the ``GET /signals`` feed params (doc 08 §3.2).

    Every field is optional: the rewrite only sets a filter it is confident the NL
    query implied. Repeatable params are lists (treated as ``IN (...)``, doc 08 §1.6).
    ``extra="forbid"`` makes the schema strict so an LLM that invents a field is
    rejected and routed through repair/fallback rather than silently accepted.
    """

    model_config = ConfigDict(extra="forbid")

    signal_type: list[SignalType] = Field(default_factory=list)
    entity_kind: list[EntityKind] = Field(default_factory=list)
    # US state codes (e.g. "WA"). Normalized to upper-case two-letter codes.
    state: list[str] = Field(default_factory=list)
    status: list[SignalStatus] = Field(default_factory=list)
    # Relevance gate, matches the feed's `min_score` (0-1, default 0.0 upstream).
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    # Date range on the signal publication time (doc 08 §3.2 published_at_gte/lt).
    published_at_gte: dt.date | None = None
    published_at_lt: dt.date | None = None

    @model_validator(mode="after")
    def _normalize_and_check(self) -> SearchFilters:
        # State codes are case-insensitive in NL ("washington" is resolved to the
        # code upstream); store them upper-cased and de-duplicated, order-stable.
        if self.state:
            seen: dict[str, None] = {}
            for code in self.state:
                seen.setdefault(code.strip().upper(), None)
            self.state = list(seen)
        # A backwards date range is incoherent; reject so repair/fallback runs.
        if (
            self.published_at_gte is not None
            and self.published_at_lt is not None
            and self.published_at_lt <= self.published_at_gte
        ):
            raise ValueError("published_at_lt must be after published_at_gte")
        return self

    def is_empty(self) -> bool:
        """True when the rewrite extracted no structured filters at all."""
        return not any(
            (
                self.signal_type,
                self.entity_kind,
                self.state,
                self.status,
                self.min_score is not None,
                self.published_at_gte is not None,
                self.published_at_lt is not None,
            )
        )


class StructuredQuery(BaseModel):
    r"""The full NL -> structured rewrite result.

    ``filters`` carry everything the feed/hybrid retrieval can pin down; ``text``
    is the residual free-text the rewrite could not turn into a filter, fed to
    full-text/semantic search (TODO I3). ``keywords`` are salient terms the model
    surfaced (kept distinct from ``text`` for optional BM25 boosting). ``entities``
    are free-text entity/geography *hints* (e.g. "Northshore School District")
    that I3's entity resolution will map to ``entity_id``\ s — we do not invent
    ids here. ``degraded`` is set when the LLM rewrite failed and we fell back to
    treating the whole input as the text query.
    """

    model_config = ConfigDict(extra="forbid")

    text: str = ""
    filters: SearchFilters = Field(default_factory=SearchFilters)
    keywords: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    degraded: bool = False


class RewriteUsage(BaseModel):
    """Token usage + model provenance for one rewrite call (doc 06 §7 accounting)."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    input_tokens: int
    output_tokens: int


class RewriteRequest(BaseModel):
    """``POST /smart-search/rewrite`` request body."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)


class RewriteResponse(BaseModel):
    """``POST /smart-search/rewrite`` response: the structured query + usage."""

    model_config = ConfigDict(extra="forbid")

    query: StructuredQuery
    usage: RewriteUsage | None = None
