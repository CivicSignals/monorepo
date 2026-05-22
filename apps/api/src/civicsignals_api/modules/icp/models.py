"""icp SQLAlchemy models.

Tables are prefixed ``icp_`` and are migrated only by this module (doc 06 §3,
§4). The ICP definition (doc 07 §icp, doc 14 §3.1, §6) is the workspace's
standing "kind of buyer I sell to" targeting config — the lens the matcher /
scorer (F3) uses to decide which signals enter a workspace's feed.

The shapes here are chosen for the **cheap pre-filter** the matcher runs on every
new signal (doc 14 §6.1): the ICP's coarse dimensions live in *queryable* columns
rather than buried in one opaque JSON blob, so the matcher can intersect 5,000
workspaces down to a handful of candidates with a single indexed SQL query:

- ``countries`` / ``states`` / ``entity_kinds`` / ``signal_types`` are ``TEXT[]``
  array columns with GIN indexes. An **empty array means "all values"** — a
  workspace that doesn't restrict by state matches every state (doc 14 §6.1).
  The matcher's filter is ``(states = '{}' OR states @> ARRAY[$signal_state])``.
- ``min_size`` / ``max_size`` are a nullable integer range (entity enrollment /
  population), compared with ``$signal_size BETWEEN min_size AND max_size``.
- ``signal_weights`` is a JSONB map ``signal_type -> weight (0..1)`` consumed by
  the scorer's per-signal-type weighting (doc 14 §6.2).
- ``keywords_required`` / ``keywords_excluded`` and the ``deal_band_*`` cents
  range are the more expensive scorer dimensions (doc 14 §6.2), kept on the row.
- ``threshold`` is the minimum score (0..100) a signal must reach to earn a
  ``signals_workspace_score`` row for this workspace (doc 07 §icp, doc 14 §5.2).

A workspace has **exactly one active ICP** (doc 14 §3.1); a partial unique index
enforces that at the DB layer. Editing an ICP triggers a backfill (F6) so the
feed reflects the new definition.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class IcpDefinition(Base):
    """A workspace's Ideal Customer Profile (doc 07 §icp, doc 14 §3.1).

    Workspace-scoped: every row belongs to one ``accounts_workspace`` and is only
    visible inside that workspace (the routes scope every query through
    ``require_workspace``). The pre-filter dimension columns are pre-indexed so
    the matcher (F3) can narrow candidate workspaces cheaply (doc 14 §6).
    """

    __tablename__ = "icp_definition"
    __table_args__ = (
        # GIN indexes on the array dimensions back the matcher's ``@>`` filter
        # (doc 14 §6.1). ``active`` is included on the signal-types index because
        # the candidate query always filters ``active = true`` first.
        Index("ix_icp_definition_countries", "countries", postgresql_using="gin"),
        Index("ix_icp_definition_states", "states", postgresql_using="gin"),
        Index("ix_icp_definition_entity_kinds", "entity_kinds", postgresql_using="gin"),
        Index("ix_icp_definition_signal_types", "signal_types", postgresql_using="gin"),
        # At most one active ICP per workspace (doc 14 §3.1). Partial index so a
        # workspace can hold any number of *inactive* definitions but only one
        # active one — set_active flips them atomically.
        Index(
            "uq_icp_definition_one_active_per_workspace",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)

    # --- Pre-filter dimensions (doc 14 §6.1). Empty array == "all values". ---
    countries: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    states: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    entity_kinds: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    signal_types: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )

    # Entity-size band (enrollment / population). NULL bound == open-ended.
    min_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # --- Scorer dimensions (doc 14 §6.2). ---
    # Per-signal-type weight map: {"rfp_posted": 1.0, "news_mention": 0.3, ...}.
    signal_weights: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    keywords_required: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    keywords_excluded: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default=text("'{}'::text[]")
    )
    # Deal-band match against the extracted amount (integer cents, doc 08 §1.1).
    deal_band_min_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deal_band_max_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Minimum score (0..100) for inclusion in the feed (doc 07 §icp, doc 14 §5.2).
    threshold: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("50"))

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
