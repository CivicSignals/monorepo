"""extraction SQLAlchemy models.

Tables are prefixed ``extraction_`` and are migrated only by this module
(doc 06 §3, §4). Models import ``Base`` from the shared declarative base so
Alembic autogenerate sees one metadata object.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base


def _new_uuid() -> uuid.UUID:
    """Application-side UUID v4 default (mirrors ingestion/recipes).

    Generating the PK in Python keeps the migration free of any server-side UUID
    function, so the tables create on a stock Postgres without ``pgcrypto``.
    """
    return uuid.uuid4()


class RelevanceDecision(Base):
    """A Stage-2 relevance-gate decision, stored for retrospective FP/FN analysis.

    Doc 19 §3.4: classifier outputs are persisted so that, after a window, we can
    label false positives (gate said "yes" but no signal extracted) and false
    negatives (gate said "no" but a signal existed) and tune the prompt. One row
    per gate evaluation, including the prefilter short-circuits (``method`` records
    which path produced the verdict).

    Owned solely by the ``extraction`` module (table prefix ``extraction_``).
    """

    __tablename__ = "extraction_relevance_decision"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Reference to the fetched document this decision is about (doc 18's
    # ``raw_document`` row, owned by ingestion). Stored as the opaque id string;
    # no FK across the module boundary (doc 06 §3).
    raw_document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    recipe_id: Mapped[str] = mapped_column(String(128), nullable=False)

    relevant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # Matched coarse signal-type categories (doc 19 §3.2 ``categories``).
    categories: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # How the verdict was produced: ``classifier`` (LLM ran), ``assume_relevant``
    # (recipe prefilter short-circuit), or a fallback path. Provenance for tuning.
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="classifier")
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Retrospective analysis groups by recipe over time (doc 19 §3.4, §12.4).
        Index("ix_extraction_relevance_decision_recipe_created", "recipe_id", "created_at"),
        Index("ix_extraction_relevance_decision_raw_document", "raw_document_id"),
    )


# Job lifecycle states (doc 19 §1 funnel + §2-§7 stages). ``skipped_irrelevant``
# is a *success* terminal state — the relevance gate (E8) dropped the document
# cheaply, which is the intended outcome for ~70-80% of docs, not a failure.
JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_SKIPPED_IRRELEVANT = "skipped_irrelevant"

# Pipeline stages, in funnel order (doc 19 §2-§7). ``stage`` records how far a job
# got, so a failed/in-flight job is observable down to the stage that was running.
STAGE_FETCH = "fetch"
STAGE_PARSE = "parse"
STAGE_RELEVANCE = "relevance"
STAGE_EXTRACT = "extract"
STAGE_SCORE = "score"
STAGE_DEDUPE = "dedupe"
STAGE_STORE = "store"


class ExtractionJob(Base):
    """One run of the extraction pipeline over a single raw document (E1).

    Tracks a document's journey through the funnel (doc 19 §1): fetch -> parse ->
    relevance gate -> extract -> score -> dedupe -> store. ``status`` is the
    terminal/in-flight state; ``stage`` records the last stage entered, so an
    operator can see *where* a job is or where it failed (doc 19 §12.1 tracks the
    end-to-end p95 lag this row makes observable). ``error`` carries the failure
    message after retries are exhausted (the dead-letter, mirroring D11's durable
    miss record).

    Owned solely by the ``extraction`` module (table prefix ``extraction_``). The
    ``raw_document_id`` references ingestion's ``ingestion_raw_document`` row but is
    **not** a cross-module FK (doc 06 §3) — same convention as
    ``extraction_relevance_decision``. One job per ``raw_document_id`` (a UNIQUE
    constraint) so a re-enqueue of the same document reuses its job rather than
    forking the funnel; the enqueue path is a get-or-create on this key.
    """

    __tablename__ = "extraction_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)

    # The document this job processes (ingestion-owned id; no cross-module FK).
    raw_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Recipe slug that produced the document (provenance for per-recipe scorecards,
    # doc 19 §12.4). Stored as the slug string, not a FK (recipes are versioned YAML).
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{JOB_STATUS_PENDING}'")
    )
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # How many times the chain has been retried (observability for the retry path).
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # One job per document — the enqueue seam is a get-or-create on this key
        # so a re-enqueue is idempotent (doc 19 §1: replay against the snapshot).
        UniqueConstraint("raw_document_id", name="extraction_job_raw_document_uq"),
        # The beat task scans for pending work; this index keeps that scan cheap.
        Index("ix_extraction_job_status_created", "status", "created_at"),
    )


class ExtractionCandidate(Base):
    """A candidate signal record emitted by the extract stage (doc 19 §5; E1).

    The pipeline's extract stage produces zero-or-more candidates per document.
    Until the strict typed signal schema (E4) and the global ``signals_signal``
    table land, candidates are persisted here so **nothing is lost** — E4/E5 read
    these back to validate, score, dedupe, and promote them into real signals.

    ``signal_type`` is the coarse type guess (doc 19 §5.1); ``fields`` is the
    permissive intermediate payload (the E4 seam — a strict ``ExtractedEntities``/
    per-type schema replaces this dict). ``confidence`` is the score-stage output
    (a passthrough stub until E6). ``dedup_key`` is computed by the dedupe stage
    (a passthrough stub until E5). ``status`` mirrors the candidate lifecycle:
    ``new`` (freshly emitted), later ``merged``/``promoted``/``rejected`` by E5.

    Owned solely by the ``extraction`` module (table prefix ``extraction_``).
    """

    __tablename__ = "extraction_candidate"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)

    # The job + document that produced this candidate (extraction-owned job id;
    # ingestion-owned document id — neither is a cross-module FK to ingestion).
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    raw_document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Coarse signal-type guess (doc 19 §5.1). Nullable: the extract stage may emit
    # a candidate it could not type, leaving classification to E4.
    signal_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Permissive intermediate payload. TODO E4: replace with the strict typed
    # per-signal-type schema; this dict is the seam E4 reads from.
    fields: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Score-stage output (doc 19 §6.2). TODO E6: real confidence blend; passthrough now.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Dedupe-stage output (doc 19 §7.1). TODO E5: real per-type key; passthrough now.
    dedup_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # E9 OCR flags (doc 19 §2.2): set when the OCR fallback ran during parse.
    # ``ocr_used`` — OCR was invoked (pdfplumber returned < 200 chars for > 5 page PDF).
    # ``ocr_truncated`` — document exceeded the 100-page cap; only first-50 + last-25
    # pages were OCR'd (doc 19 §2.2).
    ocr_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    ocr_truncated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'new'"))

    # How the extract stage produced this candidate: ``llm`` (gateway extraction),
    # ``deterministic`` (Stage-3 pass-1, future), or ``fallback`` (LLM output
    # unparseable — degraded, doc 19 §4.1). Provenance for E4/E5.
    extraction_method: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'llm'")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_extraction_candidate_job", "job_id"),
        Index("ix_extraction_candidate_raw_document", "raw_document_id"),
        # E5 dedupe looks up candidates by their computed key (doc 19 §7.2).
        Index("ix_extraction_candidate_dedup_key", "dedup_key"),
    )
