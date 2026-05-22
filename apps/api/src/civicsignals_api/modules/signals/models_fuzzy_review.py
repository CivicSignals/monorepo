"""SQLAlchemy model for the fuzzy-dedupe human review queue (doc 19 §7.4; E10).

The ``signals_fuzzy_review`` table holds one row per fuzzy-match candidate that was
routed to human review instead of auto-merging (the first ``GRADUATION_COUNT`` fuzzy
matches per signal type, per doc 19 §7.4). A reviewer approves (triggering a merge
via :func:`~signals.fuzzy_dedupe.apply_fuzzy_review`) or rejects (letting the
candidate stand as a distinct signal).

Table is owned by the ``signals`` module (``signals_`` prefix, doc 06 §3).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base

# Review lifecycle statuses (doc 19 §7.4).
REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_APPROVED = "approved"
REVIEW_STATUS_REJECTED = "rejected"


class SignalFuzzyReview(Base):
    """A fuzzy-dedupe match awaiting (or decided by) human review (doc 19 §7.4; E10).

    When a high-stakes signal type (``rfp_posted`` or ``contract_expiring``) has not
    yet graduated to auto-merge (fewer than ``GRADUATION_COUNT`` reviewed rows), each
    fuzzy match creates one row here instead of merging immediately. A reviewer then
    calls the approve/reject endpoint.

    Fields:
    - ``candidate_signal_id``: the just-inserted candidate signal (it is a full
      ``signals_signal`` row; its sources are merged into ``matched_signal_id`` on
      approval, doc 19 §7.3).
    - ``matched_signal_id``: the existing signal the ANN query identified as a
      near-duplicate.
    - ``similarity``: the cosine similarity at which the match was found (0-1).
    - ``signal_type``: slug (same as ``signals_signal.signal_type``).
    - ``status``: pending / approved / rejected.
    - ``reviewed_at``: when the human acted (NULL while pending).
    - ``reviewer_note``: optional free-text from the reviewer (for the audit trail).

    Loose refs (UUIDs stored as native Postgres UUIDs, no FK across the module
    boundary — doc 06 §3). Both signal ids reference ``signals_signal.id`` but the FK
    is intentionally absent so the review table does not create a cross-module hard
    dependency.
    """

    __tablename__ = "signals_fuzzy_review"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    # The newly-inserted candidate and the existing matched signal (both in
    # signals_signal; stored as loose UUID refs per doc 06 §3 module-boundary rule).
    candidate_signal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matched_signal_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    # Cosine similarity at which the match was found (0-1, rounded to 6 dp).
    similarity: Mapped[float] = mapped_column(Float, nullable=False)

    # The high-stakes signal type (rfp_posted / contract_expiring).
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Review lifecycle: pending → approved | rejected.
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{REVIEW_STATUS_PENDING}'")
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Per-type status scan (the review admin list + graduation count query).
        Index(
            "signals_fuzzy_review_type_status_idx",
            "signal_type",
            "status",
        ),
        # Look up all reviews touching a given signal (audit trail).
        Index("signals_fuzzy_review_candidate_idx", "candidate_signal_id"),
        Index("signals_fuzzy_review_matched_idx", "matched_signal_id"),
    )
