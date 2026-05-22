"""pipeline SQLAlchemy models — J1, J3.

Tables are prefixed ``pipeline_`` and are migrated only by this module
(doc 06 §3, §4; doc 07 §2 pipeline section).

Three tables:
- ``pipeline_stage``         — workspace-specific Kanban columns (name, order, default flag).
- ``pipeline_item``          — an opportunity being tracked through the pipeline.
- ``pipeline_item_activity`` — append-only activity log for each pipeline item (J3).

Ownership:
- All tables carry ``workspace_id`` FK → ``accounts_workspace``.
- ``pipeline_item.owner_id`` → ``accounts_member`` (the assignee).
- ``pipeline_item.signal_id`` is a *nullable loose reference* to the future
  ``signals_signal`` table (doc 07 §2 pipeline item). The signals module is not
  yet built, so this is a plain UUID column without a DB-level FK.
  # TODO J1-signal-fk: add REFERENCES signals_signal(id) once signals land.

Stages per PRD F14.1:
  Saved → Researching → Contacted → Meeting Booked → Qualified → Proposal / RFP
  → Won / Lost / Disqualified

These nine default stages are seeded by ``services.provision_default_stages``
on first workspace use.

Item status enum mirrors the stage names for clarity but is distinct — an item
can be in ``Won`` stage while its administrative status is ``active`` until the
rep archives it. Status is kept simple for J1; J3 (activity log) adds richer
lifecycle tracking.

Activity types (J3):
  created | stage_changed | assigned | value_changed | comment | integration_push
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class ItemStatus(StrEnum):
    """Lifecycle status for a pipeline item (J1, PRD F14).

    ``active`` is the working state; ``archived`` hides the item from the
    default Kanban view without deleting it so J5 reporting can still count it.
    ``won`` / ``lost`` / ``disqualified`` are terminal outcomes that correspond
    to the final-column stages but are tracked explicitly so J5 can roll them up
    across stage renamings.
    """

    ACTIVE = "active"
    WON = "won"
    LOST = "lost"
    DISQUALIFIED = "disqualified"
    ARCHIVED = "archived"


# Default stage definitions seeded per workspace (PRD F14.1).
# Each entry: (name, position, is_default).
# ``is_default`` marks the stage new items land in when no explicit stage is given.
DEFAULT_STAGES: list[tuple[str, int, bool]] = [
    ("Saved", 0, True),
    ("Researching", 1, False),
    ("Contacted", 2, False),
    ("Meeting Booked", 3, False),
    ("Qualified", 4, False),
    ("Proposal / RFP", 5, False),
    ("Won", 6, False),
    ("Lost", 7, False),
    ("Disqualified", 8, False),
]


class PipelineStage(Base):
    """A Kanban column in a workspace's pipeline (J1, PRD F14.1, doc 07 §2).

    Stages are per-workspace so each tenant can rename or reorder them without
    affecting others. ``position`` is a zero-based integer that controls the
    column order; the service layer keeps positions contiguous on CRUD.

    ``is_default`` marks the stage new items are placed in when no explicit stage
    is supplied. At most one stage per workspace should have ``is_default = true``
    (enforced in the service layer, not at DB level — partial indexes for
    multi-column unique on nullable columns are messy in Alembic autogenerate).

    ``name`` is unique within a workspace so J2 (Kanban UI) can display stage
    chips without ambiguity.
    """

    __tablename__ = "pipeline_stage"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_pipeline_stage_ws_name"),
        CheckConstraint("position >= 0", name="ck_pipeline_stage_position_gte0"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PipelineItem(Base):
    """An opportunity / lead being tracked through the workspace's pipeline (J1).

    PRD F14: users add a signal or entity to "My Pipeline" with a stage and
    notes. J2 supplies drag-and-drop stage transitions; J3 adds activity log;
    J4 covers items not tied to a signal; J5 adds reporting rollups.

    **signal_id** — nullable, loose reference to the future ``signals_signal``
    table. Stored as plain UUID so the pipeline module compiles without
    depending on the signals module's models (doc 06 §3 "no module imports
    another module's internals"). The application layer validates the UUID exists
    when signals land (J4/E4 integration).
    # TODO J1-signal-fk: add REFERENCES signals_signal(id) once signals table exists.

    **owner_id** — nullable loose reference to ``accounts_member.id``. Stored as
    plain UUID (no DB-level FK) so the pipeline module can be used independently
    of the members endpoint (B6). The application layer will validate membership
    when the members API lands.
    # TODO J1-owner-fk: add REFERENCES accounts_member(id) once B6 members API lands.

    **value_estimate** — optional deal value in USD (NUMERIC 15,2), e.g. the
    RFP estimated value pre-filled from the linked signal's ``details_jsonb``.
    """

    __tablename__ = "pipeline_item"
    __table_args__ = (
        CheckConstraint(
            "value_estimate IS NULL OR value_estimate >= 0",
            name="ck_pipeline_item_value_gte0",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_stage.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # TODO J1-signal-fk: add REFERENCES signals_signal(id) once signals table exists.
    signal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    # TODO J1-owner-fk: add REFERENCES accounts_member(id) once B6 members API lands.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NUMERIC(15, 2) for dollar values — avoids float imprecision (doc 07 §2).
    value_estimate: Mapped[Decimal | None] = mapped_column(Numeric(15, 2), nullable=True)
    status: Mapped[ItemStatus] = mapped_column(
        Enum(
            ItemStatus,
            name="pipeline_item_status",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=ItemStatus.ACTIVE.value,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Activity log (J3)
# ---------------------------------------------------------------------------


class ActivityType(StrEnum):
    """The kind of event recorded in ``pipeline_item_activity`` (J3).

    Values:
    - ``created``           — item was first created.
    - ``stage_changed``     — item moved to a different stage (payload: from/to stage ids/names).
    - ``assigned``          — owner (assignee) changed (payload: old/new owner_id).
    - ``value_changed``     — value_estimate updated (payload: old/new value).
    - ``comment``           — free-text note added by a team member.
    - ``integration_push``  — item pushed to an external integration (K-chain seam).
                              # TODO K: record_activity called by integration push handler.
    """

    CREATED = "created"
    STAGE_CHANGED = "stage_changed"
    ASSIGNED = "assigned"
    VALUE_CHANGED = "value_changed"
    COMMENT = "comment"
    INTEGRATION_PUSH = "integration_push"


class PipelineItemActivity(Base):
    """Append-only activity log entry for a pipeline item (J3, doc 07 §2).

    Every meaningful mutation on a :class:`PipelineItem` — creation, stage
    transition, assignment, value edit — produces one row here. Comments from
    team members are stored as ``activity_type = 'comment'`` with the comment
    text in ``payload['text']``.

    **Append-only contract:** no UPDATE or DELETE is ever issued against this
    table.  The service layer enforces this.

    Columns:
    - ``item_id``       — FK → pipeline_item (CASCADE delete so rows are pruned
                          when the item is hard-deleted).
    - ``workspace_id``  — denormalised for workspace-scoped queries without a
                          JOIN (consistent with the modulith's workspace-scope rule).
    - ``actor_id``      — nullable UUID; the ``accounts_member.id`` of whoever
                          triggered the event. NULL for system-generated entries.
                          Stored as a loose UUID (no DB FK) so the pipeline module
                          doesn't depend on the accounts module's table layout.
                          # TODO J3-actor-fk: add FK once B6 members API is stable.
    - ``activity_type`` — :class:`ActivityType` enum.
    - ``payload``       — JSONB context bag, schema varies by type:
                          stage_changed: {from_stage_id, to_stage_id, from_stage_name, to_stage_name}
                          assigned:      {old_owner_id, new_owner_id}
                          value_changed: {old_value, new_value}
                          comment:       {text}
                          integration_push: {target, result, ...}  # filled by K-chain
    - ``created_at``    — event timestamp (server default; no updated_at — append-only).
    """

    __tablename__ = "pipeline_item_activity"
    __table_args__ = (
        Index(
            "ix_pipeline_item_activity_item_time",
            "item_id",
            "created_at",
            postgresql_ops={"created_at": "DESC"},
        ),
        Index("ix_pipeline_item_activity_workspace_time", "workspace_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_item.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    # TODO J3-actor-fk: add REFERENCES accounts_member(id) once B6 members API lands.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    activity_type: Mapped[ActivityType] = mapped_column(
        Enum(
            ActivityType,
            name="pipeline_activity_type",
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
