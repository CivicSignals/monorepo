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

from sqlalchemy import DateTime, String, func
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
