"""auth SQLAlchemy models.

Tables are prefixed ``auth_`` and are migrated only by this module
(doc 06 §3, §4). The ``accounts`` module owns the user identity row
(``accounts_user``); this module owns the **credential / token lifecycle** that
hangs off it. For B1 that is the single-use email-verification token. B3
(password reset) adds ``auth_password_reset_token`` here. B4 (MFA) adds its
own ``auth_*`` table.

Tokens are never stored in cleartext: only a SHA-256 hash of the random token is
persisted, so a database leak does not expose usable verification links
(threat-model §4.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


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
