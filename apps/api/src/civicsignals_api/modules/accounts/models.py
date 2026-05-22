"""accounts SQLAlchemy models.

Tables are prefixed ``accounts_`` and are migrated only by this module
(doc 06 §3, §4). This module owns the global **User** record (doc 07 §2,
``accounts_user``). Auth *credentials verification* and the email-verification
token lifecycle live in the ``auth`` module (``auth_*`` tables); the user row
itself — identity + profile — is owned here so workspace membership (B5) and
RBAC (B7) can hang off it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class User(Base):
    """A global CivicSignals user (doc 07 §1, §2).

    Email is stored case-insensitively (CITEXT) and is unique. ``password_hash``
    is nullable so OAuth-only users (B2) and not-yet-set-password users are
    representable. ``email_verified_at`` is the verification timestamp (NULL =
    unverified). ``oauth_provider``/``oauth_subject`` are placeholders for B2.

    ``last_active_workspace_id`` (B5) records the workspace the user most
    recently switched to. It is the fallback the API uses to scope a
    session/PAT request when the ``X-Workspace-Id`` header is absent
    (doc 08 §1.4). It is nullable (a brand-new user has no workspace yet) and
    ``ON DELETE SET NULL`` so deleting a workspace does not orphan the user row.
    """

    __tablename__ = "accounts_user"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(CITEXT(), unique=True, nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)

    # OAuth seam for B2 (Google). Present now so the column is migrated once.
    oauth_provider: Mapped[str | None] = mapped_column(String, nullable=True)
    oauth_subject: Mapped[str | None] = mapped_column(String, nullable=True)

    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # B5: the workspace this user last switched to (doc 08 §1.4). FK to
    # ``accounts_workspace``; ``use_alter`` breaks the circular-FK ordering at
    # CREATE TABLE time (workspace.owner_id -> user, user.last_active -> workspace).
    last_active_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "accounts_workspace.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_accounts_user_last_active_workspace",
        ),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


class MembershipRole(StrEnum):
    """Role a user holds in a workspace (doc 07 §2 ``accounts_member.role``).

    The column exists from B5 so RBAC (B7) has somewhere to enforce against;
    B5 itself only distinguishes the workspace creator (``owner``) from invited
    members (default ``member``). Enforcement of role-gated actions is deferred
    to B7.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"


class Organization(Base):
    """A billing customer that owns one or more workspaces (doc 07 §1, §2).

    B5 auto-creates one organization per workspace at creation time so the
    ``accounts_workspace.organization_id`` FK (doc 07) is always satisfiable;
    billing (N1) hangs the Stripe customer + subscription off this row later.
    """

    __tablename__ = "accounts_organization"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String, nullable=False)
    billing_email: Mapped[str | None] = mapped_column(CITEXT(), nullable=True)
    # Stripe customer id seam for N1 (billing). Present now so it migrates once.
    stripe_customer_id: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Workspace(Base):
    """A tenant boundary (doc 07 §1, §2 ``accounts_workspace``).

    Every workspace-scoped resource hangs off this id; the ``X-Workspace-Id``
    header (doc 08 §1.4) names one of these. ``slug`` is globally unique (used in
    URLs / invites). ``owner_id`` is the user who created it (always also an
    ``owner`` member). Soft-deleted via ``deleted_at`` (doc 07 conventions).
    """

    __tablename__ = "accounts_workspace"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_organization.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    country_default: Mapped[str] = mapped_column(String(2), server_default="US", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Membership(Base):
    """A user's membership in a workspace, with a role (doc 07 §2 ``accounts_member``).

    Unique on ``(workspace_id, user_id)`` — a user joins a workspace at most
    once. ``invited_by`` / ``invited_at`` are populated by B6 (invitations);
    ``joined_at`` is set when the user accepts (or, for the creator, at
    creation). The ``role`` column is the seam B7 (RBAC) enforces against.
    """

    __tablename__ = "accounts_member"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_accounts_member_ws_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[MembershipRole] = mapped_column(
        Enum(MembershipRole, name="accounts_membership_role", native_enum=False, length=16),
        nullable=False,
        server_default=MembershipRole.MEMBER.value,
    )
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="SET NULL"),
        nullable=True,
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
