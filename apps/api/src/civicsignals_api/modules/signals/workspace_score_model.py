# SPDX-License-Identifier: AGPL-3.0-only
"""The sparse per-workspace signal score table (F3, doc 14 §5).

``signals_workspace_score`` is the crux of the scaling story (doc 14 §5.1-§5.2):
a ``signals_signal`` row is **global** (one extraction → many subscribers, doc 07
§3), so workspace relevance is captured in this separate, much smaller table — a
row exists **only** when a signal scored at/above the workspace's ICP threshold
(doc 14 §5.2, §6.2). The naive signal-by-workspace cross-product (25B rows) is never
materialized; the matcher's cheap pre-filter (doc 14 §6.1) plus the threshold gate
keep this table 3-4 orders of magnitude smaller.

Owned solely by the ``signals`` module (table prefix ``signals_``); migrated only
here (doc 06 §3, §4). It lives in its own module file (not ``models.py``) purely to
keep the two concerns readable — Alembic still autoloads it via ``signals.models``
re-exporting it.

Columns map the doc 14 §5.3 / doc 07 ``signals_workspace_score`` DDL, with the
MVP simplifications the task scopes:

- ``workspace_id`` / ``signal_id`` — the (workspace, global-signal) pair; unique
  together (doc 14 §5.3). A signal earns at most one score row per workspace.
- ``score`` — the blended 0..100 relevance score (doc 14 §6.2; F1's ``threshold``
  is on the same 0..100 scale). Stored ``Numeric(5, 2)`` so the weighted component
  sum keeps two decimals without float drift.
- ``score_breakdown`` — the per-component contribution map the scorer produced
  (doc 14 §6.2); F4's "Why this signal?" panel renders human-readable bullets from
  it, so the structured breakdown is persisted rather than recomputed.
- ``status`` — the feed lifecycle (doc 14 §5.3 / G4): ``new`` → ``reviewed`` →
  ``pinned`` / ``pushed`` / ``dismissed``. The feed query filters on it.
- ``matched_via`` — informational provenance (doc 14 §5.3): ``icp`` here; saved
  searches (doc 14 §8) record ``saved_search:<uuid>`` once N-something lands.
- ``matched_signal_type`` / ``matched_states`` / … — the **matched flags**: which
  ICP dimensions the candidate satisfied, surfaced so F4 can explain the match
  without re-running the matcher.

Indexes target the hot feed query (doc 14 §5.3): ``(workspace_id, status,
score DESC, created_at DESC)`` is a single index range scan; ``signal_id`` backs the
fan-out / backfill upsert lookups and workspace-deletion cleanup (doc 14 §10.5).

Partitioning (doc 14 §5.4, monthly by ``created_at``) is deferred — the MVP keeps a
plain table; the partition-management beat job is a later op task. The composite
index is identical either way, so the feed query is forward-compatible.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7

# The feed lifecycle states (doc 14 §5.3, G4). A score row starts ``new`` and the
# feed/user moves it onward. ``new``/``reviewed``/``pinned`` stay in the feed;
# ``dismissed`` is hidden; ``pushed`` (sent to a CRM) is informational.
SCORE_STATUS_NEW = "new"
SCORE_STATUS_REVIEWED = "reviewed"
SCORE_STATUS_PINNED = "pinned"
SCORE_STATUS_PUSHED = "pushed"
SCORE_STATUS_DISMISSED = "dismissed"

SCORE_STATUSES: tuple[str, ...] = (
    SCORE_STATUS_NEW,
    SCORE_STATUS_REVIEWED,
    SCORE_STATUS_PINNED,
    SCORE_STATUS_PUSHED,
    SCORE_STATUS_DISMISSED,
)

# The default feed-visible statuses (doc 14 §5.3 feed query: new/reviewed/pinned).
FEED_VISIBLE_STATUSES: tuple[str, ...] = (
    SCORE_STATUS_NEW,
    SCORE_STATUS_REVIEWED,
    SCORE_STATUS_PINNED,
)

# How a signal entered a workspace's feed (doc 14 §5.3 ``matched_via``). F3 only
# writes ``icp``; saved-search-driven rows (doc 14 §8) would record
# ``saved_search:<uuid>`` once that path lands.
MATCHED_VIA_ICP = "icp"


class WorkspaceScore(Base):
    """One (workspace, signal) relevance score (doc 14 §5.3).

    Sparse: a row exists only for a signal that scored at/above the workspace's ICP
    threshold (doc 14 §5.2). Workspace-scoped through ``workspace_id`` — the feed
    query and every service read filter on it, so workspace A can never see
    workspace B's scores. ``signal_id`` is a global FK to ``signals_signal``.
    """

    __tablename__ = "signals_workspace_score"

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

    # The blended 0..100 relevance score (doc 14 §6.2). Numeric so the weighted
    # component sum keeps two decimals deterministically (no float drift across
    # re-scores / upserts).
    score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)

    # Per-component contribution breakdown for F4's "Why this signal?" (doc 14 §6.2):
    # ``{"signal_type_weight": .., "dimensions": .., "recency": .., "confidence": ..,
    #    "keywords": .., "semantic": .., "bullets": [..]}``. Stored, not recomputed.
    score_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{SCORE_STATUS_NEW}'")
    )
    matched_via: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text(f"'{MATCHED_VIA_ICP}'")
    )

    # --- Matched flags (doc 14 §6.2; surfaced for F4 explanation). ---
    # Which ICP dimensions this candidate satisfied. ``matched_states`` keeps the
    # actual states that intersected so F4 can say "matched state WA" (doc 14 §6.2).
    matched_signal_type: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    matched_country: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    matched_state: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    matched_entity_kind: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    matched_size_band: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    matched_keywords: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )

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
        # A signal earns at most one score row per workspace (doc 14 §5.3). The
        # upsert (matcher fan-out + F6 backfill) targets this constraint.
        UniqueConstraint("workspace_id", "signal_id", name="uq_sws_workspace_signal"),
        # The hot feed query (doc 14 §5.3): narrow by workspace + status, order by
        # score then recency — a single index range scan, sub-100ms at scale.
        Index(
            "ix_sws_workspace_status_score",
            "workspace_id",
            "status",
            text("score DESC"),
            text("created_at DESC"),
        ),
        # Fan-out / backfill upsert lookup + workspace-deletion cleanup (doc 14 §10.5).
        Index("ix_sws_signal", "signal_id"),
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in SCORE_STATUSES) + ")",
            name="signals_workspace_score_status_check",
        ),
        CheckConstraint("score >= 0 AND score <= 100", name="signals_workspace_score_range_check"),
    )
