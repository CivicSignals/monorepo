# SPDX-License-Identifier: AGPL-3.0-only
"""The per-(workspace, signal) user feedback table (F5, doc 14 §12).

``signals_signal_feedback`` captures a user's verdict on whether a scored signal was
actually relevant to their workspace — the "negative training" loop (doc 14 §12) that
re-weights *subsequent* scores for the workspace. One of three kinds:

- ``relevant`` — the signal was a good match; nudges the signal type's weight **up**.
- ``not_relevant`` — a false positive; nudges the signal type's weight **down**.
- ``wrong_extraction`` — the *extraction* was wrong (mis-parsed fields / wrong entity),
  independent of relevance. This is an **extraction-quality** signal, **not** a scoring
  one, so it never feeds the re-weighting math — it is recorded + surfaced for review
  (count flagged on the detail/feed read) and handed off to the extraction-quality
  pipeline (QA-7 / E-epic). See ``signals.workspace_scoring.derive_signal_type_overrides``.

A user may **change** their verdict (the upsert flips ``kind``) or **retract** it
(delete the row) — so the natural key is ``(workspace_id, signal_id, user_id)``: at
most one live verdict per user per (workspace, signal). The aggregate the scorer reads
sums every member's verdicts per signal type (doc 14 §12).

Owned solely by the ``signals`` module (table prefix ``signals_``); migrated only here
(doc 06 §3, §4). Lives in its own module file (re-exported via ``signals.models`` so
Alembic's ``env.py`` autoloads it) to keep the F3 score table + the F5 feedback table
readable as separate concerns.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7

# The three feedback verdicts (doc 14 §12; F5 task). ``relevant`` / ``not_relevant``
# drive the per-workspace re-weighting; ``wrong_extraction`` is an extraction-quality
# flag that does NOT alter scoring (recorded + surfaced only).
FEEDBACK_RELEVANT = "relevant"
FEEDBACK_NOT_RELEVANT = "not_relevant"
FEEDBACK_WRONG_EXTRACTION = "wrong_extraction"

FEEDBACK_KINDS: tuple[str, ...] = (
    FEEDBACK_RELEVANT,
    FEEDBACK_NOT_RELEVANT,
    FEEDBACK_WRONG_EXTRACTION,
)

# The verdicts that re-weight scoring (doc 14 §12). ``wrong_extraction`` is excluded —
# it is an extraction-quality signal, not a relevance one (see the module docstring).
FEEDBACK_SCORING_KINDS: frozenset[str] = frozenset({FEEDBACK_RELEVANT, FEEDBACK_NOT_RELEVANT})


class SignalFeedback(Base):
    """One user's relevance verdict on one (workspace, signal) pair (F5, doc 14 §12).

    Workspace-scoped through ``workspace_id`` — the aggregate the scorer reads and
    every service query filter on it, so workspace A's feedback can never re-weight
    workspace B (doc 14 §12). ``signal_id`` is a global FK to ``signals_signal``;
    ``user_id`` records who gave the verdict (nullable + ``SET NULL`` so a deleted user
    does not erase the workspace's accumulated training signal).
    """

    __tablename__ = "signals_signal_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    signal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("signals_signal.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Who gave the verdict. Nullable + SET NULL so a deleted account does not drop the
    # workspace's accumulated feedback (the aggregate stays intact for re-weighting).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="SET NULL"),
        nullable=True,
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False)

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
        # One live verdict per user per (workspace, signal): changing a verdict is an
        # upsert on this key, retracting is a delete (doc 14 §12). The POST endpoint's
        # ON CONFLICT targets this constraint.
        UniqueConstraint(
            "workspace_id", "signal_id", "user_id", name="uq_signal_feedback_ws_signal_user"
        ),
        # The aggregate the scorer reads: every verdict for a workspace, grouped by the
        # signal's type (the per-type re-weight, doc 14 §12). Indexed by workspace so
        # the aggregate query is a single workspace-scoped scan.
        Index("ix_signal_feedback_workspace", "workspace_id"),
        # Detail/feed reads of "this user's verdict on this signal" + signal-deletion
        # cleanup go through (workspace_id, signal_id).
        Index("ix_signal_feedback_workspace_signal", "workspace_id", "signal_id"),
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in FEEDBACK_KINDS) + ")",
            name="signals_signal_feedback_kind_check",
        ),
    )
