"""DB-backed tests for the C6 contact-correction flow.

Tests:
- ``report_contact_invalid`` marks the contact (status/verified/confidence).
- ``mark_bounced`` convenience wrapper sets kind="bounced".
- Calling the service multiple times is idempotent-friendly (accumulates rows,
  further lowers confidence, keeps status as "bounced"/"invalid").
- Workspace scoping: the correction row records workspace_id + reporter_id.
- HTTP route: POST /contacts/{id}/report-invalid returns 201 with the updated
  contact + correction row; requires auth + workspace header.
- HTTP route: 404 for unknown contact; 422 for bad kind.

DSN is resolved from ``CONTACTS_TEST_DSN`` / ``DATABASE_DIRECT_URL`` /
``DATABASE_URL`` — same as the sibling ``test_db.py``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import Table, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import civicsignals_api.modules.entities.models  # noqa: F401 — populates Base.metadata with entities_entity
from civicsignals_api.db import Base
from civicsignals_api.modules.contacts.models import ContactCorrection
from civicsignals_api.modules.contacts.services import (
    ContactInput,
    CorrectionInput,
    ProvenanceInput,
    create_contact,
    mark_bounced,
    report_contact_invalid,
)

_DSN = (
    os.environ.get("CONTACTS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

_ENTITY_TABLE_NAMES = ("entities_entity", "entities_kind", "entities_geo")
_CONTACT_TABLE_NAMES = (
    "contacts_correction",
    "contacts_title",
    "contacts_phone",
    "contacts_email",
    "contacts_contact",
)
_ALL_TABLE_NAMES = _ENTITY_TABLE_NAMES + _CONTACT_TABLE_NAMES


def _get_tables(names: tuple[str, ...]) -> list[Table]:
    return [Base.metadata.tables[n] for n in names]


def _drop_managed_cascade(conn) -> None:  # type: ignore[no-untyped-def]
    for tbl in reversed(_ALL_TABLE_NAMES):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl} CASCADE")


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(_drop_managed_cascade)
        await conn.run_sync(
            Base.metadata.create_all,
            tables=_get_tables(_ALL_TABLE_NAMES),
        )

    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_managed_cascade)
        await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_entity(session: AsyncSession) -> uuid.UUID:
    eid = uuid.UUID(int=0x01977F0AAAAABBBBCCCCDDDDEEEEEFFF & ((1 << 128) - 1))
    await session.execute(
        text(
            "INSERT INTO entities_entity (id, type, status, name, country)"
            " VALUES (:id, 'school_district', 'active', 'Northshore SD', 'US')"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": str(eid)},
    )
    await session.flush()
    return eid


_WORKSPACE_ID = uuid.uuid4()
_REPORTER_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_contact_invalid_marks_status(session: AsyncSession) -> None:
    """report_contact_invalid sets status='invalid' and clears verified."""
    async with session.begin():
        eid = await _insert_entity(session)

    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(
                entity_id=eid,
                name="Test Person",
                canonical_email="test@nsd.org",
                provenance=ProvenanceInput(verified=True, confidence=0.9),
            ),
        )

    async with session.begin():
        result = await report_contact_invalid(
            session,
            CorrectionInput(
                contact_id=contact.id,
                workspace_id=_WORKSPACE_ID,
                reporter_id=_REPORTER_ID,
                kind="wrong_email",
                reason="Email bounced on send",
            ),
        )

    assert result.contact.status == "invalid"
    assert result.contact.verified is False
    assert result.contact.last_verified_at is None
    assert result.contact.reported_invalid_at is not None
    assert result.contact.bounce_count == 1
    # Confidence should be lowered from 0.9 by 0.2.
    assert result.contact.confidence is not None
    assert result.contact.confidence == pytest.approx(0.7, abs=0.01)


@pytest.mark.asyncio
async def test_report_bounced_sets_bounced_status(session: AsyncSession) -> None:
    """kind='bounced' sets status='bounced'."""
    async with session.begin():
        eid = await _insert_entity(session)

    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(entity_id=eid, name="Bouncer", canonical_email="bouncer@nsd.org"),
        )

    async with session.begin():
        result = await report_contact_invalid(
            session,
            CorrectionInput(
                contact_id=contact.id,
                workspace_id=_WORKSPACE_ID,
                reporter_id=_REPORTER_ID,
                kind="bounced",
            ),
        )

    assert result.contact.status == "bounced"


@pytest.mark.asyncio
async def test_mark_bounced_convenience_wrapper(session: AsyncSession) -> None:
    """mark_bounced is a convenience alias that always uses kind='bounced'."""
    async with session.begin():
        eid = await _insert_entity(session)

    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(entity_id=eid, name="Person", canonical_email="person@nsd.org"),
        )

    async with session.begin():
        result = await mark_bounced(
            session,
            contact_id=contact.id,
            workspace_id=_WORKSPACE_ID,
            reporter_id=_REPORTER_ID,
            reason="CRM push failed",
        )

    assert result.contact.status == "bounced"
    assert result.correction.kind == "bounced"
    assert result.correction.reason == "CRM push failed"


@pytest.mark.asyncio
async def test_correction_records_workspace_and_reporter(session: AsyncSession) -> None:
    """The correction row stores workspace_id and reporter_id for audit trail."""
    async with session.begin():
        eid = await _insert_entity(session)

    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(entity_id=eid, name="Person", canonical_email="ws_test@nsd.org"),
        )

    ws = uuid.uuid4()
    reporter = uuid.uuid4()

    async with session.begin():
        result = await report_contact_invalid(
            session,
            CorrectionInput(
                contact_id=contact.id,
                workspace_id=ws,
                reporter_id=reporter,
                kind="other",
                correction="correct@nsd.org",
            ),
        )

    assert result.correction.workspace_id == ws
    assert result.correction.reporter_id == reporter
    assert result.correction.correction == "correct@nsd.org"


@pytest.mark.asyncio
async def test_report_idempotent_accumulates(session: AsyncSession) -> None:
    """Calling report_contact_invalid multiple times accumulates rows and lowers confidence."""
    async with session.begin():
        eid = await _insert_entity(session)

    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(
                entity_id=eid,
                name="Multi-report",
                canonical_email="multi@nsd.org",
                provenance=ProvenanceInput(confidence=1.0),
            ),
        )

    async with session.begin():
        await report_contact_invalid(
            session,
            CorrectionInput(
                contact_id=contact.id,
                workspace_id=_WORKSPACE_ID,
                reporter_id=_REPORTER_ID,
                kind="bounced",
            ),
        )

    async with session.begin():
        result2 = await report_contact_invalid(
            session,
            CorrectionInput(
                contact_id=contact.id,
                workspace_id=uuid.uuid4(),
                reporter_id=uuid.uuid4(),
                kind="wrong_email",
            ),
        )

    # Two correction rows should exist.
    rows = list(
        (
            await session.execute(
                select(ContactCorrection).where(ContactCorrection.contact_id == contact.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    # Bounce count incremented twice.
    assert result2.contact.bounce_count == 2
    # Confidence lowered twice from 1.0 (1.0 - 0.2 - 0.2 = 0.6).
    assert result2.contact.confidence is not None
    assert result2.contact.confidence == pytest.approx(0.6, abs=0.02)


@pytest.mark.asyncio
async def test_report_not_found_raises(session: AsyncSession) -> None:
    """report_contact_invalid raises ValueError for an unknown contact id."""
    with pytest.raises(ValueError, match="not found"):
        async with session.begin():
            await report_contact_invalid(
                session,
                CorrectionInput(
                    contact_id=uuid.uuid4(),
                    workspace_id=_WORKSPACE_ID,
                    reporter_id=_REPORTER_ID,
                    kind="other",
                ),
            )


@pytest.mark.asyncio
async def test_correction_invalid_kind_raises() -> None:
    """CorrectionInput raises ValueError for an unknown kind."""
    with pytest.raises(ValueError, match="kind must be one of"):
        CorrectionInput(
            contact_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            reporter_id=uuid.uuid4(),
            kind="nonsense",
        )


# ---------------------------------------------------------------------------
# HTTP route tests
# ---------------------------------------------------------------------------


def _make_auth_token(user_id: uuid.UUID) -> str:
    """Create a minimal JWT access token for the given user_id (test only)."""
    from civicsignals_api.modules.auth import services as auth_svc

    return str(auth_svc.issue_access_token(user_id))


async def _insert_auth_fixtures(
    engine: object,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    ws_ids: list[uuid.UUID],
    member_ws_id: uuid.UUID,
) -> None:
    """Insert user + org + workspace(s) + membership via raw SQL (avoids ORM field name drift)."""
    from sqlalchemy.ext.asyncio import AsyncEngine

    assert isinstance(engine, AsyncEngine)
    # Ensure accounts tables exist (already migrated in the live DB; idempotent).
    from civicsignals_api.db import Base as _Base

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(
            _Base.metadata.create_all,
            tables=[
                _Base.metadata.tables[t]
                for t in (
                    "accounts_user",
                    "accounts_organization",
                    "accounts_workspace",
                    "accounts_member",
                )
                if t in _Base.metadata.tables
            ],
        )

    # Insert in FK-dependency order: org → user (no ws yet) → workspace → membership.
    async with AsyncSession(engine, expire_on_commit=False) as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO accounts_organization (id, name)"
                " VALUES (:id, :name) ON CONFLICT DO NOTHING"
            ),
            {"id": str(org_id), "name": f"Org-{org_id}"},
        )
        # User without last_active_workspace_id first (avoids circular FK).
        await s.execute(
            text(
                "INSERT INTO accounts_user"
                " (id, email, password_hash, email_verified_at)"
                " VALUES (:id, :email, :pw, now()) ON CONFLICT DO NOTHING"
            ),
            {
                "id": str(user_id),
                "email": f"user-{user_id}@example.com",
                "pw": "x",
            },
        )
        for ws_id in ws_ids:
            await s.execute(
                text(
                    "INSERT INTO accounts_workspace (id, organization_id, name, slug, owner_id)"
                    " VALUES (:id, :org_id, :name, :slug, :owner_id) ON CONFLICT DO NOTHING"
                ),
                {
                    "id": str(ws_id),
                    "org_id": str(org_id),
                    "name": f"WS-{ws_id}",
                    "slug": f"ws-{ws_id}",
                    "owner_id": str(user_id),
                },
            )
        # Now update last_active_workspace_id.
        await s.execute(
            text(
                "UPDATE accounts_user SET last_active_workspace_id = :ws_id WHERE id = :id"
            ),
            {"ws_id": str(member_ws_id), "id": str(user_id)},
        )
        await s.execute(
            text(
                "INSERT INTO accounts_member (id, workspace_id, user_id, role)"
                " VALUES (gen_random_uuid(), :ws_id, :user_id, :role) ON CONFLICT DO NOTHING"
            ),
            {"ws_id": str(member_ws_id), "user_id": str(user_id), "role": "member"},
        )


@pytest.mark.asyncio
async def test_route_report_invalid_returns_201(session: AsyncSession) -> None:
    """POST /contacts/{id}/report-invalid returns 201 with updated contact + correction."""
    import httpx

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app

    # --- setup contact ---
    async with session.begin():
        eid = await _insert_entity(session)

    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(
                entity_id=eid,
                name="Route Test",
                canonical_email="route@nsd.org",
                provenance=ProvenanceInput(verified=True, confidence=0.9),
            ),
        )
    await session.rollback()

    assert _DSN is not None
    engine = create_async_engine(_DSN)

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()

    await _insert_auth_fixtures(
        engine,
        user_id=user_id,
        org_id=org_id,
        ws_ids=[ws_id],
        member_ws_id=ws_id,
    )

    token = _make_auth_token(user_id)

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine, expire_on_commit=False) as req_sess:
            yield req_sess

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Valid report.
            resp = await client.post(
                f"/api/v1/contacts/{contact.id}/report-invalid",
                json={"kind": "bounced", "reason": "email bounced"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Workspace-Id": str(ws_id),
                },
            )
            assert resp.status_code == 201, resp.text
            body = resp.json()
            assert body["contact"]["status"] == "bounced"
            assert body["contact"]["verified"] is False
            assert body["contact"]["bounce_count"] == 1
            assert body["correction"]["kind"] == "bounced"
            assert body["correction"]["reason"] == "email bounced"
            assert body["correction"]["workspace_id"] == str(ws_id)
            assert body["correction"]["reporter_id"] == str(user_id)

            # 404 for unknown contact.
            resp404 = await client.post(
                f"/api/v1/contacts/{uuid.uuid4()}/report-invalid",
                json={"kind": "other"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Workspace-Id": str(ws_id),
                },
            )
            assert resp404.status_code == 404

            # 401 without auth.
            resp_noauth = await client.post(
                f"/api/v1/contacts/{contact.id}/report-invalid",
                json={"kind": "other"},
            )
            assert resp_noauth.status_code == 401

            # 422 for invalid kind.
            resp_bad = await client.post(
                f"/api/v1/contacts/{contact.id}/report-invalid",
                json={"kind": "nonsense"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Workspace-Id": str(ws_id),
                },
            )
            assert resp_bad.status_code == 422

    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_route_workspace_scoped(session: AsyncSession) -> None:
    """POST /contacts/{id}/report-invalid enforces workspace membership (404 for non-member)."""
    import httpx

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app

    async with session.begin():
        eid = await _insert_entity(session)

    async with session.begin():
        contact = await create_contact(
            session,
            ContactInput(entity_id=eid, name="WS Person", canonical_email="wsperson@nsd.org"),
        )
    await session.rollback()

    assert _DSN is not None
    engine = create_async_engine(_DSN)

    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    ws_id_member = uuid.uuid4()
    ws_id_other = uuid.uuid4()

    await _insert_auth_fixtures(
        engine,
        user_id=user_id,
        org_id=org_id,
        ws_ids=[ws_id_member, ws_id_other],
        member_ws_id=ws_id_member,  # user is only a member of ws_id_member
    )

    token = _make_auth_token(user_id)

    async def _override() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(engine, expire_on_commit=False) as req_sess:
            yield req_sess

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Non-member workspace → 404 (workspace hidden from non-members).
            resp = await client.post(
                f"/api/v1/contacts/{contact.id}/report-invalid",
                json={"kind": "other"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Workspace-Id": str(ws_id_other),
                },
            )
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
