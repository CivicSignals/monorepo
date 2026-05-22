"""admin SQLAlchemy models.

Tables are prefixed ``admin_`` and are migrated only by this module
(doc 06 §3, §4). The ``AuditEvent`` table is the append-only audit log for the
whole modulith (doc 07 §2 ``audit_event``, B9).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class AuditEvent(Base):
    """An immutable audit log entry (doc 07 §2 ``audit_event``, B9).

    Append-only: no UPDATE or DELETE is ever issued against this table. Every
    significant auth/membership/token action in the modulith is recorded here,
    either via the in-process event bus (:mod:`civicsignals_api.events`) or by
    calling :func:`civicsignals_api.modules.admin.services.record_audit_event`
    directly.

    Columns:
    - ``workspace_id`` — nullable (system-level events have no workspace).
    - ``actor_user_id`` — nullable (system/automated events may have no user).
    - ``actor_token_id`` — nullable (API-token-authenticated requests).
    - ``action`` — dot-notated string, e.g. ``auth.login``, ``member.role_changed``.
    - ``target_type`` / ``target_id`` — what was acted upon (nullable).
    - ``metadata`` — JSONB bag of extra context (email, old/new role, …).
    - ``ip`` — INET (nullable; populated when request context provides it).
    - ``occurred_at`` — event timestamp (DB server default; not ``updated_at`` —
      there are no updates).
    """

    __tablename__ = "admin_audit_event"
    __table_args__ = (
        Index(
            "ix_admin_audit_event_workspace_time",
            "workspace_id",
            "occurred_at",
            postgresql_ops={"occurred_at": "DESC"},
        ),
        Index("ix_admin_audit_event_action", "action"),
        Index("ix_admin_audit_event_actor_user_id", "actor_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    # Nullable: workspace-scoped events populate this; system events do not.
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # Intentionally *no* FK: the audit log must survive workspace deletion
        # (audit entries are evidence; they must not cascade-delete).
        nullable=True,
        index=True,
    )

    # Who did it.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        # No FK: user rows may be deleted; history must be retained.
        nullable=True,
    )
    actor_token_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # What happened.
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # What it happened to.
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Extra context (email, IP, old/new values, …).
    metadata_: Mapped[dict[str, object] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
        default=dict,
    )

    # Network provenance (best-effort; not always available).
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
