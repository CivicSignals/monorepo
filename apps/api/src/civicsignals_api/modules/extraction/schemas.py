"""Pydantic request/response shapes for the extraction module (doc 06 §3).

These cover the Stage-2 relevance gate (doc 19 §3, E8): the cheap LLM that
decides whether a fetched document is worth running through full extraction.
Also carries the two-pass entity extraction types (doc 19 §4; E11).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# E11: Two-pass entity extraction result types (doc 19 §4)
# ---------------------------------------------------------------------------


class ExtractedEntity(BaseModel):
    """One entity mention surfaced by the two-pass extraction (doc 19 §4; E11).

    ``raw_name`` is the name as it appeared in the document.  After the entity-
    linking pass, ``entity_id`` / ``entity_name`` are filled from the canonical
    ``entities_entity`` row (doc 19 §4.3).  ``resolution_pending=True`` means
    the mention was not matched to a known entity and the signal will be stored
    with the raw string + human-review flag until the entity is resolved or
    created.
    """

    model_config = ConfigDict(extra="forbid")

    raw_name: str
    entity_id: uuid.UUID | None = None
    entity_name: str | None = None
    confidence: float = 0.0
    # True until a canonical entity row is linked (doc 19 §4.3).
    resolution_pending: bool = True


class EntityExtractionResult(BaseModel):
    """Full output of the two-pass entity extraction stage (doc 19 §4; E11).

    ``entities`` is the deduplicated, linked list of organisation/vendor
    mentions.  The detailed sub-lists (``organizations``, ``persons``, …) carry
    the raw LLM output for provenance and downstream scoring.

    ``extraction_method`` records which passes ran:
    - ``"deterministic"``  — Pass 1 only (sufficient confidence + type coverage).
    - ``"llm_assisted"``   — Pass 1 + Pass 2 (Sonnet refinement triggered).
    - ``"failed"``         — Pass 1 failed entirely; result is empty + degraded.

    ``degraded=True`` when Pass 2 was needed but failed; the result carries
    Pass-1 data only, with lower implicit confidence.
    """

    model_config = ConfigDict(extra="forbid")

    entities: list[ExtractedEntity] = Field(default_factory=list)
    # Detailed sub-lists from the merged LLM output (permissive dicts; typed
    # schemas are E4's responsibility at promotion time).
    organizations: list[dict[str, object]] = Field(default_factory=list)
    persons: list[dict[str, object]] = Field(default_factory=list)
    monetary_amounts: list[dict[str, object]] = Field(default_factory=list)
    dates: list[dict[str, object]] = Field(default_factory=list)
    products_categories: list[str] = Field(default_factory=list)
    vendors_mentioned: list[dict[str, object]] = Field(default_factory=list)
    contract_terms_mentions: list[dict[str, object]] = Field(default_factory=list)
    raw_keywords: list[str] = Field(default_factory=list)
    # Overall extraction confidence (0.0-0.95).
    extraction_confidence: float = 0.0
    extraction_warnings: list[str] = Field(default_factory=list)
    extraction_method: str = "deterministic"
    degraded: bool = False


# The candidate signal-type categories the classifier is asked about. These line
# up with the signal-type taxonomy (doc 19 §5.1) but stay deliberately coarse —
# the gate only needs "is any of these plausibly present?", not a precise type.
RELEVANCE_CATEGORIES: tuple[str, ...] = (
    "procurement",
    "budget",
    "grant",
    "leadership_change",
    "strategic_plan",
    "board_agenda_item",
)


class DocumentRef(BaseModel):
    """Minimal reference + content for a fetched document handed to the gate.

    Just enough to classify and to record the decision for retrospective FP/FN
    analysis (doc 19 §3.4); the full ``raw_document`` row lives in S3 + Postgres
    (doc 18) and is referenced by ``raw_document_id``.
    """

    model_config = ConfigDict(extra="forbid")

    raw_document_id: str
    recipe_id: str
    source: str | None = None
    text: str = ""


class RelevanceVerdict(BaseModel):
    """The structured verdict produced by the Stage-2 relevance gate.

    ``relevant``/``confidence`` drive the funnel (doc 19 §3.2); ``categories`` are
    the matched signal-type hints; ``method`` records whether the LLM ran or a
    recipe prefilter short-circuited it. ``model``/``prompt_name``/
    ``prompt_version`` are provenance for the decision record.
    """

    model_config = ConfigDict(extra="forbid")

    relevant: bool
    confidence: float = Field(ge=0.0, le=1.0)
    categories: list[str] = Field(default_factory=list)
    reason: str | None = None
    method: str = "classifier"
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    truncated: bool = False


class RelevanceDecisionRecord(BaseModel):
    """A persisted decision row (mirrors ``extraction_relevance_decision``)."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    raw_document_id: str
    recipe_id: str
    relevant: bool
    confidence: float
    categories: list[str]
    reason: str | None
    method: str
    model: str | None
    prompt_name: str | None
    prompt_version: str | None
    created_at: datetime


# --- E1: the orchestration pipeline (doc 19 §1-§7) -----------------------------


class ParsedDocument(BaseModel):
    """Stage-1 output: clean text + metadata extracted from the raw bytes (doc 19 §2).

    The parse stage turns the stored raw bytes (pdf/html/text) into a single
    ``text`` string the rest of the funnel consumes. ``content_type`` and
    ``char_count`` are carried for downstream decisions (e.g. the OCR trigger,
    doc 19 §2.2 — E9). ``degraded`` flags a lossy parse (an unrecognised content
    type fell back to a best-effort decode).

    ``ocr_used`` is set when the OCR fallback ran (E9; doc 19 §2.2) — i.e. pdfplumber
    returned < 200 chars for a > 5-page PDF and OCR was invoked.
    ``ocr_truncated`` is set when the PDF exceeded the 100-page cap and only the
    first-50 + last-25 pages were OCR'd (doc 19 §2.2).
    """

    model_config = ConfigDict(extra="forbid")

    raw_document_id: uuid.UUID
    recipe_id: str
    source_url: str | None = None
    content_type: str | None = None
    text: str = ""
    char_count: int = 0
    degraded: bool = False
    # E9 OCR flags (doc 19 §2.2). Both default False for non-PDF / non-OCR paths.
    ocr_used: bool = False
    ocr_truncated: bool = False


class CandidateRecord(BaseModel):
    """A permissive intermediate candidate signal record (the E4 seam; doc 19 §5).

    Emitted by the extract stage, carried through score/dedupe/store. ``fields`` is
    a free-form dict until E4's strict per-signal-type schema replaces it; nothing
    here is validated as hard as a real signal yet (that is the E4 gate, doc 19
    §6.1). ``confidence`` + ``band`` are filled by the score stage (E6 blend, doc 19
    §6.2-§6.3): ``confidence`` is the blended extraction confidence, ``band`` the
    mapped action band (``rejected`` candidates are dropped before store).
    ``dedup_key`` is filled by the dedupe stage (E5 stub).

    ``entity_extraction`` carries the two-pass entity extraction result (E11,
    doc 19 §4) that ran before signal-type detection.  It is ``None`` when the
    entity extraction step was skipped (e.g. very short docs or test fixtures
    that pre-date E11).  The ``entity_id`` / ``entity_name`` from the first
    resolved entity in ``entity_extraction.entities`` are propagated into the
    store step (doc 19 §4.3) so the candidate links to the right canonical row.
    """

    model_config = ConfigDict(extra="forbid")

    signal_type: str | None = None
    fields: dict[str, object] = Field(default_factory=dict)
    confidence: float | None = None
    # The E6 confidence band (doc 19 §6.3): "normal" | "degraded" | "pending_review"
    # | "rejected". Filled by the score stage; carried to the store step which lifts
    # it onto the ``signals_signal`` row flags. ``None`` until the score stage runs.
    band: str | None = None
    dedup_key: str | None = None
    extraction_method: str = "llm"
    # E11: two-pass entity extraction result (doc 19 §4).  ``None`` when the step
    # was skipped or not yet wired (backward compatible with pre-E11 code).
    entity_extraction: EntityExtractionResult | None = None


class ExtractionCandidateRecord(BaseModel):
    """A persisted ``extraction_candidate`` row (the read seam E4/E5 pick up)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    raw_document_id: uuid.UUID
    recipe_id: str
    signal_type: str | None
    fields: dict[str, object]
    confidence: float | None
    dedup_key: str | None
    status: str
    extraction_method: str
    created_at: datetime


class ExtractionJobRecord(BaseModel):
    """A persisted ``extraction_job`` row (pipeline observability; doc 19 §12.1)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    raw_document_id: uuid.UUID
    recipe_id: str
    status: str
    stage: str | None
    attempts: int
    error: str | None
    created_at: datetime
    updated_at: datetime
