"""Pydantic request/response shapes for the extraction module (doc 06 §3).

These cover the Stage-2 relevance gate (doc 19 §3, E8): the cheap LLM that
decides whether a fetched document is worth running through full extraction.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

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
    """

    model_config = ConfigDict(extra="forbid")

    raw_document_id: uuid.UUID
    recipe_id: str
    source_url: str | None = None
    content_type: str | None = None
    text: str = ""
    char_count: int = 0
    degraded: bool = False


class CandidateRecord(BaseModel):
    """A permissive intermediate candidate signal record (the E4 seam; doc 19 §5).

    Emitted by the extract stage, carried through score/dedupe/store. ``fields`` is
    a free-form dict until E4's strict per-signal-type schema replaces it; nothing
    here is validated as hard as a real signal yet (that is the E4 gate, doc 19
    §6.1). ``confidence`` is filled by the score stage (E6 stub); ``dedup_key`` by
    the dedupe stage (E5 stub).
    """

    model_config = ConfigDict(extra="forbid")

    signal_type: str | None = None
    fields: dict[str, object] = Field(default_factory=dict)
    confidence: float | None = None
    dedup_key: str | None = None
    extraction_method: str = "llm"


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
