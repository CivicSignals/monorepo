"""extraction SQLAlchemy models.

Tables are prefixed ``extraction_`` and are migrated only by this module
(doc 06 §3, §4). Models import ``Base`` from the shared declarative base so
Alembic autogenerate sees one metadata object.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base


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
