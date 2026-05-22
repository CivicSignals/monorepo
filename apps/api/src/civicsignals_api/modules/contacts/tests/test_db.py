"""DB-backed tests for the contacts schema and service API (C2).

These run against a live Postgres resolved from (in order) ``CONTACTS_TEST_DSN``,
``DATABASE_DIRECT_URL``, or ``DATABASE_URL`` — so they execute automatically in CI
(which sets ``DATABASE_DIRECT_URL`` to a throwaway ``civicsignals_test`` DB) and
for any dev with the stack up, while skipping cleanly when no DB is configured.

Each test runs in a throwaway schema created fresh from the SQLAlchemy metadata
(both the entities tables — needed because contacts FK to ``entities_entity`` —
and the contacts tables). Tables are torn down after each test run so tests are
independent and repeatable.

DSN must be an ``asyncpg`` URL, e.g.::

    CONTACTS_TEST_DSN=postgresql+asyncpg://civic:civic@localhost:55432/civicsignals
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import ModuleType

import pytest
import pytest_asyncio
from sqlalchemy import Table, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from civicsignals_api.db import Base
from civicsignals_api.modules.contacts.models import (
    Contact,
    ContactEmail,
    ContactPhone,
    ContactTitle,
)
from civicsignals_api.modules.contacts.services import (
    ContactInput,
    EmailInput,
    PhoneInput,
    ProvenanceInput,
    TitleInput,
    create_contact,
    get_contact,
    list_contacts_for_entity,
    upsert_contact,
)

_DSN = (
    os.environ.get("CONTACTS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

# Tables to create/drop — contacts tables first in the FK order, entities tables
# last (contacts FK → entities_entity, so entities must exist first at create
# time, be dropped last). We create them in dependency order and drop in reverse.
_ENTITY_TABLE_NAMES = ("entities_entity", "entities_kind", "entities_geo")
_CONTACT_TABLE_NAMES = (
    "contacts_title",
    "contacts_phone",
    "contacts_email",
    "contacts_contact",
)

# All tables we manage in this test (entities first, then contacts).
_ALL_TABLE_NAMES = _ENTITY_TABLE_NAMES + _CONTACT_TABLE_NAMES


def _get_tables(names: tuple[str, ...]) -> list[Table]:
    return [Base.metadata.tables[n] for n in names]


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN)

    # Drop in reverse dependency order (contacts first, then entities).
    drop_order = list(reversed(_ALL_TABLE_NAMES))
    create_order = list(_ALL_TABLE_NAMES)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        # Drop existing tables if present from a previous failed run.
        await conn.run_sync(Base.metadata.drop_all, tables=_get_tables(tuple(drop_order)))
        # Create in dependency order.
        await conn.run_sync(Base.metadata.create_all, tables=_get_tables(tuple(create_order)))

    try:
        async with AsyncSession(engine) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=_get_tables(tuple(drop_order)))
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_entity(session: AsyncSession) -> uuid.UUID:
    """Insert a minimal entities_entity row and return its id."""
    eid = uuid.UUID(int=0x01977F0AAAAABBBBCCCCDDDDEEEEEFFF & ((1 << 128) - 1))
    # Use raw SQL to avoid importing entities models (module isolation).
    await session.execute(
        text(
            "INSERT INTO entities_entity (id, type, status, name, country)"
            " VALUES (:id, 'school_district', 'active', 'Northshore School District', 'US')"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": str(eid)},
    )
    await session.flush()
    return eid


# ---------------------------------------------------------------------------
# Migration test
# ---------------------------------------------------------------------------


def _load_migration_module() -> ModuleType:
    """Import the C2 migration by file path (alembic/versions isn't a package)."""
    import importlib.util
    from pathlib import Path

    api_root = Path(__file__).resolve().parents[5]
    versions_dir = api_root / "alembic" / "versions"
    matches = list(versions_dir.glob("*contacts_contact_email_phone_title_tables.py"))
    assert matches, f"C2 migration file not found in {versions_dir}"
    spec = importlib.util.spec_from_file_location("c2_contacts_migration", matches[0])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration_upgrade(conn):  # type: ignore[no-untyped-def]
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    migration = _load_migration_module()
    for tbl in reversed(_CONTACT_TABLE_NAMES):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl} CASCADE")
    with Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
    rows = conn.exec_driver_sql(
        "SELECT tablename FROM pg_tables WHERE tablename LIKE 'contacts_%'"
    ).fetchall()
    return {r[0] for r in rows}


@pytest.mark.asyncio
async def test_contacts_migration_applies(session: AsyncSession) -> None:
    """The generated Alembic DDL creates exactly the four contacts tables on PG."""
    assert _DSN is not None
    engine = create_async_engine(_DSN)
    try:
        async with engine.begin() as conn:
            tables = await conn.run_sync(_run_migration_upgrade)
        assert tables == {
            "contacts_contact",
            "contacts_email",
            "contacts_phone",
            "contacts_title",
        }
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Create / upsert idempotency with provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_contact_with_provenance(session: AsyncSession) -> None:
    """Creating a contact stores all provenance fields correctly."""
    async with session.begin():
        entity_id = await _insert_entity(session)

    observed = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(
                entity_id=entity_id,
                name="Dr. Lisa Hong",
                canonical_email="lhong@nsd.org",
                title="Director of Curriculum",
                department="Teaching and Learning",
                provenance=ProvenanceInput(
                    source="nsd.org/staff-directory",
                    source_url="https://www.nsd.org/staff-directory/curriculum",
                    confidence=0.95,
                    observed_at=observed,
                    verified=True,
                    last_verified_at=observed,
                ),
            ),
        )

    fetched = await get_contact(session, contact.id)
    assert fetched is not None
    assert fetched.name == "Dr. Lisa Hong"
    assert fetched.canonical_email == "lhong@nsd.org"
    assert fetched.title == "Director of Curriculum"
    assert fetched.department == "Teaching and Learning"
    assert fetched.source == "nsd.org/staff-directory"
    assert fetched.source_url == "https://www.nsd.org/staff-directory/curriculum"
    assert fetched.confidence == pytest.approx(0.95)
    assert fetched.verified is True
    assert fetched.last_verified_at is not None


@pytest.mark.asyncio
async def test_upsert_idempotent_on_entity_email(session: AsyncSession) -> None:
    """Upserting the same (entity, email) pair twice updates the row, not duplicates it."""
    async with session.begin():
        entity_id = await _insert_entity(session)

    async with session.begin():
        c1 = await upsert_contact(
            session,
            ContactInput(
                entity_id=entity_id,
                name="John Doe",
                canonical_email="jdoe@nsd.org",
                title="CIO",
                provenance=ProvenanceInput(source="nsd.org", confidence=0.8),
            ),
        )

    async with session.begin():
        c2 = await upsert_contact(
            session,
            ContactInput(
                entity_id=entity_id,
                name="John Doe",
                canonical_email="jdoe@nsd.org",
                title="Chief Information Officer",  # updated title
                provenance=ProvenanceInput(source="nsd.org/v2", confidence=0.99),
            ),
        )

    # Same row, same id — not a new row.
    assert c1.id == c2.id

    # Row count stays at 1.
    result = await session.execute(
        select(func.count()).select_from(Contact).where(Contact.entity_id == entity_id)
    )
    assert result.scalar_one() == 1

    # Latest provenance wins.
    updated = await get_contact(session, c1.id)
    assert updated is not None
    assert updated.title == "Chief Information Officer"
    assert updated.source == "nsd.org/v2"
    assert updated.confidence == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_upsert_without_email_creates_new_rows(session: AsyncSession) -> None:
    """Contacts with no canonical_email always create a new row (two emailless people are distinct)."""
    async with session.begin():
        entity_id = await _insert_entity(session)

    async with session.begin():
        c1 = await upsert_contact(session, ContactInput(entity_id=entity_id, name="Person A"))
        c2 = await upsert_contact(session, ContactInput(entity_id=entity_id, name="Person B"))

    assert c1.id != c2.id
    result = await session.execute(
        select(func.count()).select_from(Contact).where(Contact.entity_id == entity_id)
    )
    assert result.scalar_one() == 2


@pytest.mark.asyncio
async def test_email_and_phone_upserted_with_provenance(session: AsyncSession) -> None:
    """Child email and phone rows are upserted with provenance on each creation."""
    async with session.begin():
        entity_id = await _insert_entity(session)

    email_prov = ProvenanceInput(
        source="nsd.org/staff",
        source_url="https://www.nsd.org/staff",
        confidence=1.0,
        verified=True,
    )
    phone_prov = ProvenanceInput(source="nsd.org/staff", confidence=0.7)

    async with session.begin():
        contact = await upsert_contact(
            session,
            ContactInput(
                entity_id=entity_id,
                name="Test Person",
                canonical_email="test@nsd.org",
                emails=[
                    EmailInput(
                        email="test@nsd.org",
                        is_primary=True,
                        email_status="valid",
                        provenance=email_prov,
                    )
                ],
                phones=[
                    PhoneInput(
                        phone="+14255550100",
                        phone_type="direct",
                        is_primary=True,
                        provenance=phone_prov,
                    )
                ],
            ),
        )

    # Verify email row.
    email_rows = list(
        (await session.execute(select(ContactEmail).where(ContactEmail.contact_id == contact.id)))
        .scalars()
        .all()
    )
    assert len(email_rows) == 1
    assert email_rows[0].email == "test@nsd.org"
    assert email_rows[0].is_primary is True
    assert email_rows[0].email_status == "valid"
    assert email_rows[0].source == "nsd.org/staff"
    assert email_rows[0].verified is True

    # Verify phone row.
    phone_rows = list(
        (await session.execute(select(ContactPhone).where(ContactPhone.contact_id == contact.id)))
        .scalars()
        .all()
    )
    assert len(phone_rows) == 1
    assert phone_rows[0].phone == "+14255550100"
    assert phone_rows[0].phone_type == "direct"
    assert phone_rows[0].confidence == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_title_history_appended(session: AsyncSession) -> None:
    """Title rows are append-only — a second upsert with the same title is a no-op."""
    async with session.begin():
        entity_id = await _insert_entity(session)

    async with session.begin():
        contact = await upsert_contact(
            session,
            ContactInput(
                entity_id=entity_id,
                name="Leader",
                canonical_email="leader@nsd.org",
                titles=[
                    TitleInput(
                        title="Superintendent",
                        is_current=True,
                        provenance=ProvenanceInput(source="nsd.org"),
                    )
                ],
            ),
        )

    # Second upsert with same title → silently ignored (DO NOTHING).
    async with session.begin():
        await upsert_contact(
            session,
            ContactInput(
                entity_id=entity_id,
                name="Leader",
                canonical_email="leader@nsd.org",
                titles=[
                    TitleInput(
                        title="Superintendent",
                        is_current=True,
                        provenance=ProvenanceInput(source="nsd.org/v2"),
                    )
                ],
            ),
        )

    title_rows = list(
        (await session.execute(select(ContactTitle).where(ContactTitle.contact_id == contact.id)))
        .scalars()
        .all()
    )
    # Append-only: still only 1 row for the same title.
    assert len(title_rows) == 1


# ---------------------------------------------------------------------------
# List for entity (FK) + cursor pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_contacts_for_entity(session: AsyncSession) -> None:
    """list_contacts_for_entity returns all contacts for the given entity id."""
    async with session.begin():
        entity_id = await _insert_entity(session)

    emails = [f"person{i}@nsd.org" for i in range(7)]
    async with session.begin():
        for i, email in enumerate(emails):
            await create_contact(
                session,
                ContactInput(
                    entity_id=entity_id,
                    name=f"Person {i}",
                    canonical_email=email,
                    provenance=ProvenanceInput(source="nsd.org"),
                ),
            )

    page = await list_contacts_for_entity(session, entity_id, limit=100)
    assert len(page.items) == 7
    assert page.next_cursor is None
    assert all(c.entity_id == entity_id for c in page.items)


@pytest.mark.asyncio
async def test_list_contacts_cursor_pagination(session: AsyncSession) -> None:
    """Cursor pagination walks all contacts for an entity without duplicates."""
    async with session.begin():
        entity_id = await _insert_entity(session)

    async with session.begin():
        for i in range(12):
            await create_contact(
                session,
                ContactInput(
                    entity_id=entity_id,
                    name=f"Contact {i}",
                    canonical_email=f"c{i}@nsd.org",
                ),
            )

    seen: list[uuid.UUID] = []
    cursor: str | None = None
    pages = 0
    while True:
        page = await list_contacts_for_entity(session, entity_id, cursor=cursor, limit=5)
        seen.extend(c.id for c in page.items)
        pages += 1
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
        assert pages < 10, "pagination did not terminate"

    assert len(seen) == 12
    assert len(set(seen)) == 12
    # Results are ordered by id (UUID v7 → time-ordered).
    assert seen == sorted(seen)
    assert pages > 1


@pytest.mark.asyncio
async def test_get_contact_not_found(session: AsyncSession) -> None:
    """get_contact returns None for an unknown id."""
    result = await get_contact(session, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# Verified / stale indicator (C4 UI support, C2 req 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verified_and_stale_fields(session: AsyncSession) -> None:
    """last_verified_at and verified are persisted and queryable (C4 stale UI)."""
    async with session.begin():
        entity_id = await _insert_entity(session)

    verified_at = datetime(2026, 5, 20, 0, 0, tzinfo=UTC)
    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(
                entity_id=entity_id,
                name="Verified Person",
                canonical_email="verified@nsd.org",
                status="stale",
                provenance=ProvenanceInput(
                    verified=True,
                    last_verified_at=verified_at,
                ),
            ),
        )

    fetched = await get_contact(session, contact.id)
    assert fetched is not None
    assert fetched.verified is True
    assert fetched.status == "stale"
    assert fetched.last_verified_at is not None


# ---------------------------------------------------------------------------
# HTTP routes smoke test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_routes_list_get(session: AsyncSession) -> None:
    """The contacts read endpoints serve data without X-Workspace-Id (global, not workspace-scoped).

    Driven through in-process ASGI transport (httpx.AsyncClient) so the request
    runs through the full FastAPI request lifecycle.
    """
    import httpx

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app

    async with session.begin():
        entity_id = await _insert_entity(session)

    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(
                entity_id=entity_id,
                name="Route Test Person",
                canonical_email="routetest@nsd.org",
                provenance=ProvenanceInput(source="test", confidence=0.9),
            ),
        )
    await session.rollback()

    assert _DSN is not None
    engine = create_async_engine(_DSN)

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine) as req_session:
            yield req_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # List with entity_id filter — no workspace header.
            resp = await client.get("/api/v1/contacts", params={"entity_id": str(entity_id)})
            assert resp.status_code == 200
            body = resp.json()
            assert body["items"]
            assert body["items"][0]["name"] == "Route Test Person"

            # Get one.
            get_resp = await client.get(f"/api/v1/contacts/{contact.id}")
            assert get_resp.status_code == 200
            assert get_resp.json()["canonical_email"] == "routetest@nsd.org"
            assert get_resp.json()["source"] == "test"

            # Unknown id → RFC 7807 problem+json 404.
            missing = await client.get(f"/api/v1/contacts/{uuid.uuid4()}")
            assert missing.status_code == 404
            assert "application/problem+json" in missing.headers["content-type"]
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
