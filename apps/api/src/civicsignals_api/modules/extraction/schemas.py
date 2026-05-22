"""Pydantic request/response shapes for the extraction module (doc 06 §3).

These cover the Stage-2 relevance gate (doc 19 §3, TODO E8): the cheap LLM that
decides whether a fetched document is worth running through full extraction.
"""

from __future__ import annotations

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
