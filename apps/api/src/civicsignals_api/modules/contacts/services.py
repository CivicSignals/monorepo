"""Public service interface for the contacts module (doc 06 §3, C2).

Other modules call contacts only through the functions defined here — never by
importing contacts's models or routes directly (doc 06 §3). The contact
directory is **global** (doc 07 §3: "Contacts are global per Entity. We never
store workspace-private contact records."); these functions take an explicit
``AsyncSession`` with no workspace scoping.

Reads:
- :func:`get_contact` — by id.
- :func:`list_contacts_for_entity` — cursor-paginated list of contacts for one
  entity (doc 06 §5 — keyset on UUID v7 id, never offset).

Writes:
- :func:`create_contact` / :func:`upsert_contact` — idempotent upsert on the
  stable key ``(entity_id, canonical_email)`` (doc 07 §2 UNIQUE constraint).
  Provenance fields are stored per-record (doc 16 §18).

Entity refs: access entities only via ``entities/services.py``; never import
entities models here (doc 06 §3 module isolation).
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Contact, ContactEmail, ContactPhone, ContactTitle

# Cursor pagination defaults (doc 06 §5). Hard cap keeps an unbounded ``limit``
# from scanning the full contacts table.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


# ---------------------------------------------------------------------------
# Cursor helpers (keyset on UUID v7 id — time-ordered, stable total order).
# ---------------------------------------------------------------------------


def encode_cursor(contact_id: uuid.UUID) -> str:
    """Encode a UUID as an opaque base64 cursor token (doc 08)."""
    return base64.urlsafe_b64encode(contact_id.bytes).decode("ascii")


def decode_cursor(cursor: str) -> uuid.UUID:
    """Decode a cursor token back to a UUID; raises ``ValueError`` if malformed."""
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# Data transfer objects for reads and writes.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContactPage:
    """A cursor-paginated page of contacts (doc 06 §5).

    ``next_cursor`` is ``None`` on the last page; otherwise it is the opaque
    token the client passes back as ``?cursor=…`` to fetch the next page.
    """

    items: list[Contact]
    next_cursor: str | None


@dataclass(slots=True)
class ProvenanceInput:
    """Provenance fields for a create/upsert operation (doc 16 §18).

    All fields are optional at the call site; callers supply what they know.
    """

    source: str | None = None
    source_url: str | None = None
    source_recipe_id: uuid.UUID | None = None
    confidence: float | None = None
    observed_at: datetime | None = None
    verified: bool = False
    last_verified_at: datetime | None = None


@dataclass(slots=True)
class ContactInput:
    """All data for a create-or-upsert operation on one contact (C2 req 2).

    ``entity_id`` plus ``canonical_email`` form the idempotency key. If
    ``canonical_email`` is ``None`` the contact has no known email and a new row
    is always created (two emailless contacts at the same entity are distinct
    people).
    """

    entity_id: uuid.UUID
    name: str
    canonical_email: str | None = None  # lowercased by the caller / services layer
    title: str | None = None
    department: str | None = None
    status: str = "active"
    attributes: dict[str, object] = field(default_factory=dict)
    provenance: ProvenanceInput = field(default_factory=ProvenanceInput)

    # Optional child rows to create/update alongside the contact.
    emails: list[EmailInput] = field(default_factory=list)
    phones: list[PhoneInput] = field(default_factory=list)
    titles: list[TitleInput] = field(default_factory=list)


@dataclass(slots=True)
class EmailInput:
    """Data for one email record (C2 req 1)."""

    email: str
    is_primary: bool = False
    email_status: str = "unverified"
    provenance: ProvenanceInput = field(default_factory=ProvenanceInput)


@dataclass(slots=True)
class PhoneInput:
    """Data for one phone record (C2 req 1)."""

    phone: str
    phone_type: str | None = None
    is_primary: bool = False
    provenance: ProvenanceInput = field(default_factory=ProvenanceInput)


@dataclass(slots=True)
class TitleInput:
    """Data for one title/history record (C2 req 1)."""

    title: str
    department: str | None = None
    is_current: bool = True
    first_observed_at: datetime | None = None
    last_observed_at: datetime | None = None
    provenance: ProvenanceInput = field(default_factory=ProvenanceInput)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def get_contact(session: AsyncSession, contact_id: uuid.UUID) -> Contact | None:
    """Fetch one contact by id, or ``None`` if it does not exist."""
    return await session.get(Contact, contact_id)


async def list_contacts_for_entity(
    session: AsyncSession,
    entity_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> ContactPage:
    """Cursor-paginated list of contacts belonging to one entity (C2 req 2, doc 06 §5).

    Ordered by ``id`` (UUID v7, time-ordered) for a stable keyset cursor. Fetches
    ``limit + 1`` rows to detect whether a next page exists without a separate
    COUNT query.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(Contact).where(Contact.entity_id == entity_id)
    if cursor is not None:
        stmt = stmt.where(Contact.id > decode_cursor(cursor))
    stmt = stmt.order_by(Contact.id).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return ContactPage(items=items, next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Writes — idempotent upsert on (entity_id, canonical_email)
# ---------------------------------------------------------------------------


def _provenance_values(p: ProvenanceInput) -> dict[str, object]:
    """Flatten a :class:`ProvenanceInput` to a column-values dict."""
    return {
        "source": p.source,
        "source_url": p.source_url,
        "source_recipe_id": p.source_recipe_id,
        "confidence": p.confidence,
        "observed_at": p.observed_at,
        "verified": p.verified,
        "last_verified_at": p.last_verified_at,
    }


async def upsert_contact(session: AsyncSession, inp: ContactInput) -> Contact:
    """Create or update a contact, idempotent on ``(entity_id, canonical_email)`` (C2 req 2).

    When ``canonical_email`` is ``None`` a new row is always inserted (two people
    at the same entity without a known email are treated as distinct). When it is
    set and a matching row exists, the existing row is updated in-place (name,
    title, department, status, provenance) so re-running ingestion is safe.

    Child ``emails``, ``phones``, and ``titles`` in ``inp`` are each upserted on
    their own stable keys (``(contact_id, email)`` / ``(contact_id, phone)``).
    Title rows are insert-only (history is append-only); duplicate
    ``(contact_id, title)`` combinations are silently ignored via ``DO NOTHING``.
    """
    canonical = inp.canonical_email.lower() if inp.canonical_email else None
    prov = _provenance_values(inp.provenance)

    contact_values: dict[str, object] = {
        "entity_id": inp.entity_id,
        "name": inp.name,
        "canonical_email": canonical,
        "title": inp.title,
        "department": inp.department,
        "status": inp.status,
        "attributes": inp.attributes,
        **prov,
    }

    if canonical is not None:
        # Upsert on (entity_id, canonical_email) partial unique index.
        # ``index_where`` matches the partial index predicate so Postgres can
        # identify the constraint (partial indexes require the predicate to be
        # stated in the ON CONFLICT clause — see Postgres §7.8.4).
        #
        # We return only the ``id`` column (not the full ORM-mapped object) to
        # avoid triggering lazy-load errors in async SQLAlchemy. After the
        # INSERT/UPDATE we fetch the row via ``session.get`` which is properly
        # awaited.
        from sqlalchemy import text as _text

        update_set = {
            k: v
            for k, v in contact_values.items()
            if k not in {"entity_id", "canonical_email", "id", "created_at"}
        }
        stmt = (
            pg_insert(Contact)
            .values(**contact_values)
            .on_conflict_do_update(
                index_elements=[Contact.entity_id, Contact.canonical_email],
                index_where=_text("canonical_email IS NOT NULL"),
                set_=update_set,
            )
            .returning(Contact.id)
        )
        result = await session.execute(stmt)
        contact_id: uuid.UUID = result.scalar_one()
        # Fetch the full ORM object through the async session.
        fetched = await session.get(Contact, contact_id)
        assert fetched is not None, "upsert returned an id but row not found"
        contact = fetched
    else:
        # No email → always insert a new row.
        contact = Contact(**contact_values)
        session.add(contact)
        await session.flush()

    # --- child rows ----------------------------------------------------------
    for email_inp in inp.emails:
        e_prov = _provenance_values(email_inp.provenance)
        e_values: dict[str, object] = {
            "contact_id": contact.id,
            "email": email_inp.email.lower(),
            "is_primary": email_inp.is_primary,
            "email_status": email_inp.email_status,
            **e_prov,
        }
        e_update = {
            k: v
            for k, v in e_values.items()
            if k not in {"contact_id", "email", "id", "created_at"}
        }
        e_stmt = (
            pg_insert(ContactEmail)
            .values(**e_values)
            .on_conflict_do_update(
                index_elements=[ContactEmail.contact_id, ContactEmail.email],
                set_=e_update,
            )
        )
        await session.execute(e_stmt)

    for phone_inp in inp.phones:
        ph_prov = _provenance_values(phone_inp.provenance)
        ph_values: dict[str, object] = {
            "contact_id": contact.id,
            "phone": phone_inp.phone,
            "phone_type": phone_inp.phone_type,
            "is_primary": phone_inp.is_primary,
            **ph_prov,
        }
        ph_update = {
            k: v
            for k, v in ph_values.items()
            if k not in {"contact_id", "phone", "id", "created_at"}
        }
        ph_stmt = (
            pg_insert(ContactPhone)
            .values(**ph_values)
            .on_conflict_do_update(
                index_elements=[ContactPhone.contact_id, ContactPhone.phone],
                set_=ph_update,
            )
        )
        await session.execute(ph_stmt)

    for title_inp in inp.titles:
        t_prov = _provenance_values(title_inp.provenance)
        t_values: dict[str, object] = {
            "contact_id": contact.id,
            "title": title_inp.title,
            "department": title_inp.department,
            "is_current": title_inp.is_current,
            "first_observed_at": title_inp.first_observed_at,
            "last_observed_at": title_inp.last_observed_at,
            **t_prov,
        }
        # Title history is append-only: insert and silently skip on duplicate.
        t_stmt = pg_insert(ContactTitle).values(**t_values).on_conflict_do_nothing()
        await session.execute(t_stmt)

    return contact


# Convenience alias — ``create_contact`` routes to the same upsert logic.
create_contact = upsert_contact
