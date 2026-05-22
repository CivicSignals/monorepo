"""contacts SQLAlchemy models (doc 07 §2 "contacts", doc 16 §11, C2).

Per-record source provenance on every contact row answers WHERE the data came
from (doc 16 §18 "store provenance per record") and supports the verified/stale
lifecycle described in doc 07 §4 ("Contact email" state machine).

Four tables, all prefixed ``contacts_`` and migrated only by this module
(doc 06 §3, §4):

- ``contacts_contact`` — a person at an entity (name, role/title, FK to
  ``entities_entity``, status, timestamps). The stable idempotency key is
  ``(entity_id, canonical_email)`` per doc 07 §2 UNIQUE(entity_id, email).
- ``contacts_email`` — verified email address(es) for a contact, with their own
  per-record provenance and the ``email_status`` lifecycle
  (unverified → valid | risky | invalid → stale).
- ``contacts_phone`` — phone number(s) with provenance.
- ``contacts_title`` — title/position history for a contact, recording when a
  role was first/last observed and from which source.

Contacts are **global**, not workspace-scoped (doc 07 §3: "Contacts are global
per Entity. We never store workspace-private contact records — privacy + cost
tradeoff intentional.").
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from civicsignals_api.db import Base

from .ids import uuid7

# Valid contact statuses.
CONTACT_STATUSES: tuple[str, ...] = ("active", "inactive", "stale")

# Email validation states (doc 07 §4 "Contact email" lifecycle).
EMAIL_STATUSES: tuple[str, ...] = ("unverified", "valid", "risky", "invalid", "stale")


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)


class Contact(Base):
    """A person at a public-sector entity (doc 07 §2, C2 req 1).

    The stable idempotency key is ``(entity_id, canonical_email)`` — if the same
    email appears for the same entity from two different sources, we UPSERT the
    existing row and update provenance rather than creating a duplicate.
    ``canonical_email`` is stored lowercased; actual email addresses with their own
    lifecycle live in ``contacts_email``.

    ``entity_id`` is a bare FK column (no SQLAlchemy relationship import) because
    contacts must not import entities' models directly — all entity access goes
    through ``entities/services.py`` (doc 06 §3).

    Per-record provenance fields (doc 16 §18 "store provenance per record"):
    ``source``, ``source_url``, ``source_recipe_id``, ``confidence``,
    ``observed_at``, ``verified``, ``last_verified_at``.
    """

    __tablename__ = "contacts_contact"

    id: Mapped[uuid.UUID] = _uuid_pk()

    # FK to entities_entity — no ORM relationship import (module isolation).
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities_entity.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Identity fields.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Department — a grouping level below title (e.g. "Teaching and Learning").
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Current/primary title stored denormalized here for fast reads; full history
    # lives in ``contacts_title``.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status (doc 07 §4 lifecycle).
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))

    # Denormalized canonical email for the unique constraint / idempotent upsert.
    # Stored lowercase. Full email record with provenance lives in contacts_email.
    canonical_email: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Per-record source provenance (doc 16 §18) --------------------------
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # FK to recipes_recipe by id only (no relationship — module isolation).
    source_recipe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # -------------------------------------------------------------------------

    attributes: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # ORM relationships to child tables (all within the contacts module).
    emails: Mapped[list[ContactEmail]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    phones: Mapped[list[ContactPhone]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    titles: Mapped[list[ContactTitle]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in CONTACT_STATUSES) + ")",
            name="contacts_contact_status_check",
        ),
        # Idempotency: same entity + same email → one contact row (doc 07 §2
        # UNIQUE(entity_id, email)). Partial index excludes NULL emails so a
        # contact without a known email doesn't conflict.
        Index(
            "contacts_contact_entity_email_uniq_idx",
            "entity_id",
            "canonical_email",
            unique=True,
            postgresql_where=text("canonical_email IS NOT NULL"),
        ),
        Index("contacts_contact_entity_idx", "entity_id"),
        Index("contacts_contact_status_idx", "status"),
    )


class ContactEmail(Base):
    """An email address belonging to a contact, with per-record provenance (C2 req 1).

    ``email_status`` tracks the verification lifecycle (doc 07 §4). Multiple
    email rows per contact are allowed (primary, alias, old address); the
    ``is_primary`` flag marks the current primary address.
    """

    __tablename__ = "contacts_email"

    id: Mapped[uuid.UUID] = _uuid_pk()
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts_contact.id", ondelete="CASCADE"),
        nullable=False,
    )

    email: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    email_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'unverified'")
    )

    # --- Per-record source provenance (doc 16 §18) --------------------------
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_recipe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # -------------------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    contact: Mapped[Contact] = relationship(back_populates="emails")

    __table_args__ = (
        CheckConstraint(
            "email_status IN (" + ", ".join(f"'{s}'" for s in EMAIL_STATUSES) + ")",
            name="contacts_email_status_check",
        ),
        # One canonical row per address per person.
        Index("contacts_email_contact_email_idx", "contact_id", "email", unique=True),
        Index("contacts_email_contact_idx", "contact_id"),
    )


class ContactPhone(Base):
    """A phone number belonging to a contact, with per-record provenance (C2 req 1).

    ``phone_type`` is free-form (``direct``, ``main``, ``cell``, ``fax``) and
    not an enum so ingestion can pass through source values without schema churn.
    """

    __tablename__ = "contacts_phone"

    id: Mapped[uuid.UUID] = _uuid_pk()
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts_contact.id", ondelete="CASCADE"),
        nullable=False,
    )

    phone: Mapped[str] = mapped_column(Text, nullable=False)
    phone_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    # --- Per-record source provenance (doc 16 §18) --------------------------
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_recipe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # -------------------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    contact: Mapped[Contact] = relationship(back_populates="phones")

    __table_args__ = (
        Index("contacts_phone_contact_idx", "contact_id"),
        # One row per (contact, phone number) pair.
        Index("contacts_phone_contact_phone_idx", "contact_id", "phone", unique=True),
    )


class ContactTitle(Base):
    """Title/position history for a contact (C2 req 1, doc 07 §2).

    Records when a title was first observed, when it was last observed, and
    whether it's still the current title. This powers the ``leadership_change``
    signal (doc 16 §11) where a new title row signals a personnel change.

    ``first_observed_at`` / ``last_observed_at`` track the active window of the
    role as seen in public directories (distinct from ``observed_at`` on the
    provenance fields, which is the timestamp of THIS specific scrape/extraction).
    """

    __tablename__ = "contacts_title"

    id: Mapped[uuid.UUID] = _uuid_pk()
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts_contact.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    first_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Per-record source provenance (doc 16 §18) --------------------------
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_recipe_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # -------------------------------------------------------------------------

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    contact: Mapped[Contact] = relationship(back_populates="titles")

    __table_args__ = (
        Index("contacts_title_contact_idx", "contact_id"),
        Index("contacts_title_contact_current_idx", "contact_id", "is_current"),
    )
