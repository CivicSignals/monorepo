"""auth SQLAlchemy models.

Tables are prefixed ``auth_`` and are migrated only by this module
(doc 06 §3, §4). The ``accounts`` module owns the user identity row
(``accounts_user``); this module owns the **credential / token lifecycle** that
hangs off it. For B1 that is the single-use email-verification token. B2 adds
``auth_oauth_identity`` to store provider/subject pairs for Google OAuth (and
future providers). B3 (password reset) adds ``auth_password_reset_token`` here.
B4 (MFA) adds its own ``auth_*`` table. B8 (API tokens) adds ``auth_api_token``
— long-lived bearer credentials (workspace + personal access tokens, doc 08
§1.3).

Tokens are never stored in cleartext: only a SHA-256 hash of the random token is
persisted, so a database leak does not expose usable verification links or API
keys (threat-model §4.2).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import ARRAY, DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class OAuthIdentity(Base):
    """A OAuth provider identity linked to a :class:`~accounts.models.User` (B2).

    Stores a ``(provider, subject)`` pair uniquely — e.g. ``("google",
    "<google-sub>")`` — so the same Google account cannot be linked to two
    different CivicSignals users. ``provider_email`` is cached from the id_token
    / userinfo so diagnostic queries can find which address was used; the
    authoritative email lives on the user row.

    The unique constraint on ``(provider, subject)`` enforces identity
    uniqueness; a second FK uniqueness constraint on ``(provider, user_id)``
    (implemented as a ``UniqueConstraint``) ensures one user has at most one
    linked Google account (and, in future, one per provider).
    """

    __tablename__ = "auth_oauth_identity"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_auth_oauth_identity_provider_subject"),
        UniqueConstraint("provider", "user_id", name="uq_auth_oauth_identity_provider_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Provider name — currently only "google" (B2); "github" / "microsoft" are
    # future follow-ups. Stored as plain text rather than a constrained Enum so
    # adding a provider requires no DDL change (only new code + data).
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Provider-issued stable subject identifier (``sub`` claim in Google's
    # id_token; equivalent in other providers). Immutable for a given account.
    subject: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Provider email cached at link time (for diagnostics + future email-change
    # detection). Not used as the login email — that is on ``accounts_user``.
    provider_email: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class EmailVerificationToken(Base):
    """A single-use email-verification token (B1).

    ``token_hash`` is the SHA-256 hex digest of the opaque token mailed to the
    user. ``used_at`` enforces single use; ``expires_at`` enforces the TTL.
    """

    __tablename__ = "auth_email_verification_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PasswordResetToken(Base):
    """A single-use password-reset token (B3).

    Mirrors the pattern of :class:`EmailVerificationToken`: only the SHA-256
    hex digest of the opaque token is stored (threat-model §4.2).

    ``consumed_at`` enforces single-use. On a successful reset all *other*
    pending reset tokens for the same user are invalidated by setting their
    ``consumed_at`` (no dangling reset links after a password change).
    """

    __tablename__ = "auth_password_reset_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ApiTokenType(StrEnum):
    """Whether a token is bound to one workspace or acts as the whole user (B8).

    - ``workspace`` — a server-to-server integration token scoped to exactly one
      workspace (doc 08 §1.3 ``cs_live_``/``cs_test_``). Issuing/revoking is an
      *admin* capability in that workspace.
    - ``personal`` — a personal access token (PAT, ``cs_pat_``) acting as the
      owning user across all of their workspaces (doc 08 §1.3). Managed by the
      owning user; the active workspace is resolved from ``X-Workspace-Id`` /
      last-active just like a session (doc 08 §1.4).
    """

    WORKSPACE = "workspace"
    PERSONAL = "personal"


class ApiToken(Base):
    """A long-lived API bearer token: workspace or personal (B8, doc 08 §1.3).

    Only the SHA-256 hex digest of the opaque token is stored (``token_hash``);
    the plaintext is shown exactly **once** at creation and is never retrievable
    again (threat-model §4.2 — "tokens hashed at rest"). ``token_prefix`` keeps a
    short non-secret fragment (e.g. ``cs_pat_a1b2``) for the management UI to
    label rows without revealing the secret.

    A ``workspace`` token has a non-null ``workspace_id`` and is scoped to that
    one tenant. A ``personal`` token has a null ``workspace_id`` and resolves to
    the owning user's full membership set. ``created_by_user_id`` is the audit
    actor (doc 08 §1.3); for personal tokens it equals ``user_id``.

    ``scopes`` is the list of granted permission strings (e.g. ``signals:read``,
    doc 08 §1.3) enforced per request by the auth dependency. ``last_used_at`` is
    updated on use for hygiene; ``expires_at`` is an optional hard expiry;
    ``revoked_at`` (non-null) disables the token immediately.
    """

    __tablename__ = "auth_api_token"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    token_type: Mapped[ApiTokenType] = mapped_column(
        Enum(
            ApiTokenType,
            name="auth_api_token_type",
            native_enum=False,
            length=16,
            # Persist the lowercase ``.value`` (workspace/personal) so the column
            # round-trips back to the enum on load (mirrors accounts_member.role).
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    token_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    # Short, non-secret display fragment of the plaintext (``cs_pat_a1b2``) for
    # the management UI; never enough to reconstruct the secret.
    token_prefix: Mapped[str] = mapped_column(String(32), nullable=False)

    # The owning user (always set: who the token acts as / on whose behalf it was
    # created). For workspace tokens this is the creator; for PATs it is the
    # subject the token authenticates as.
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Set for workspace tokens; NULL for personal tokens (which span the user's
    # whole membership set, doc 08 §1.3).
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="SET NULL"),
        nullable=True,
    )

    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")

    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def is_active(self) -> bool:
        """True iff the token is neither revoked nor past its (optional) expiry."""
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > datetime.now(UTC)
