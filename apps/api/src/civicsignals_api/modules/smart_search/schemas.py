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
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from civicsignals_api.modules.signals.schemas import SignalRead

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
        # Only keep two-letter codes — blanks and full names like "WASHINGTON"
        # are dropped here so invalid values never reach downstream query building
        # (the rewrite's repair pass should resolve names to codes; anything that
        # slips through is discarded rather than propagated).
        if self.state:
            seen: dict[str, None] = {}
            for code in self.state:
                normalized = code.strip().upper()
                if len(normalized) == 2 and normalized.isalpha():
                    seen.setdefault(normalized, None)
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
    """``POST /smart-search/rewrite`` request body.

    ``query`` is whitespace-stripped before length validation so a blank/whitespace
    -only body is rejected with a 422 rather than silently producing an empty
    rewrite. ``max_length`` guards the upstream prompt size.
    """

    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class RewriteResponse(BaseModel):
    """``POST /smart-search/rewrite`` response: the structured query + usage."""

    model_config = ConfigDict(extra="forbid")

    query: StructuredQuery
    usage: RewriteUsage | None = None


# --- Hybrid retrieval (TODO I3) ------------------------------------------------
#
# Default fusion limits + weights, kept here so they are one importable place the
# service and tests share (and the request body can override per call). Hybrid
# retrieval (doc 14 §6.2) runs three independent retrievers — vector ANN, BM25
# full-text, structured filter — and fuses the vector + BM25 rankings with weighted
# reciprocal-rank fusion (RRF), intersected with the structured filters.

# How many candidates each retriever pulls before fusion. Generous relative to the
# returned top-N so a signal ranked low by one retriever but high by another still
# enters the fused set (doc 14 §6.2).
DEFAULT_CANDIDATE_LIMIT = 100
MAX_CANDIDATE_LIMIT = 500

# Top-N returned to the caller (page size). Cursor pagination over the fused order
# (doc 06 §5); the cursor is an opaque offset into the deterministic fused ranking.
DEFAULT_TOP_N = 25
MAX_TOP_N = 100

# RRF constant `k`: a larger k flattens the contribution of rank position, a smaller
# k sharpens the top of each list. 60 is the value from the original RRF paper and a
# sane default; exposed for tuning.
DEFAULT_RRF_K = 60


class FusionWeights(BaseModel):
    """Tunable weights for the hybrid-retrieval fusion (doc 14 §6.2, TODO I3).

    The fused score for a signal is a weighted reciprocal-rank fusion of its rank in
    the vector-ANN list and its rank in the BM25 full-text list. Structured filters
    are an *intersection* (a hard gate), not a weighted term. Weights are clamped
    ``>= 0``; both zero is rejected (nothing would rank). ``rrf_k`` is the RRF
    smoothing constant. ``# TODO F3``: a per-workspace ``workspace_score`` weight is
    folded in here once F3's ``signals_workspace_score`` lands.
    """

    model_config = ConfigDict(extra="forbid")

    vector: float = Field(default=1.0, ge=0.0)
    bm25: float = Field(default=1.0, ge=0.0)
    rrf_k: int = Field(default=DEFAULT_RRF_K, ge=1)

    @model_validator(mode="after")
    def _check_not_all_zero(self) -> FusionWeights:
        if self.vector == 0.0 and self.bm25 == 0.0:
            raise ValueError("at least one of vector/bm25 weight must be > 0")
        return self


class SmartSearchRequest(BaseModel):
    """``POST /smart-search`` request body (NL query + optional explicit filters).

    ``query`` is the natural-language search box text; the rewrite (I2) turns it into
    structured filters + a residual text query. ``filters`` lets a caller pin
    additional structured constraints directly (intersected with the rewrite's), so
    a UI with filter chips can pass them without round-tripping through NL. ``top_n``
    is the page size; ``candidate_limit`` caps how many rows each retriever pulls
    before fusion. ``weights`` overrides the default fusion weights for tuning.

    ``summarize``: request an optional LLM synthesis of the top-N results (I4).
    Default ``False`` so plain search has zero LLM cost beyond the rewrite.
    The summary is generated over the first page only (no summary on paginated
    follow-up pages) and capped at :data:`SUMMARY_TOP_N` signals to bound token cost.
    # TODO I5: enforce per-workspace smart-search LLM spend budget before summarizing.
    """

    model_config = ConfigDict(extra="forbid")

    query: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    filters: SearchFilters = Field(default_factory=SearchFilters)
    top_n: int = Field(default=DEFAULT_TOP_N, ge=1, le=MAX_TOP_N)
    candidate_limit: int = Field(default=DEFAULT_CANDIDATE_LIMIT, ge=1, le=MAX_CANDIDATE_LIMIT)
    weights: FusionWeights = Field(default_factory=FusionWeights)
    cursor: str | None = None
    # I4: optional LLM synthesis of the top-N ranked results.
    summarize: bool = False


class SmartSearchResult(BaseModel):
    """One ranked hit: the global signal + its fused score and per-retriever provenance.

    ``score`` is the fused RRF score (higher = more relevant; not a calibrated 0-1
    probability — it is a ranking score). ``matched_via`` lists which retrievers
    surfaced this signal (``"vector"``, ``"bm25"``, ``"filter"``) so the UI can show
    *why* a result is here (doc 14 §6.2 explanation bullets seam). Per-retriever ranks
    are exposed for debugging/tuning and the "inspect" panel.
    """

    model_config = ConfigDict(extra="forbid")

    signal: SignalRead
    score: float
    matched_via: list[str]
    vector_rank: int | None = None
    bm25_rank: int | None = None


class SmartSearchResponse(BaseModel):
    """``POST /smart-search`` response: a ranked, cursor-paginated page of hits.

    ``summary`` is ``None`` unless the request included ``summarize=true`` **and**
    the summarizer succeeded **and** there are results to summarize. Callers must
    tolerate ``None`` even when they requested a summary (graceful degradation on
    LLM failure, I4).

    ``budget_exhausted`` (I5): ``True`` when the workspace has reached its daily
    Smart Search LLM call cap and the response was produced by keyword-only
    retrieval (BM25/FTS + structured filters) rather than the full LLM-assisted
    path (NL rewrite + vector ANN + optional summary). Results are still returned
    normally — this flag is informational, not an error. Clients may show a
    "Daily AI search limit reached — showing keyword results" notice.
    """

    model_config = ConfigDict(extra="forbid")

    results: list[SmartSearchResult]
    next_cursor: str | None = None
    # Echo back the structured rewrite so the client can show "we searched for …".
    query: StructuredQuery
    degraded: bool = False
    # I4: optional LLM summary of the top-N results. Absent (None) when not
    # requested, when there are no results, or when summarization failed.
    summary: str | None = None
    # I5: True when the daily per-workspace LLM budget was reached and the
    # response falls back to keyword-only retrieval (no LLM rewrite or summary).
    budget_exhausted: bool = False
