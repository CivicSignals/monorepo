# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP route tests for the F5 feedback endpoint (POST/DELETE /signals/{id}/feedback).

Tests:
- POST records a verdict (200) and the detail view (GET) reflects it as ``feedback``.
- POST again with a different kind changes the verdict (no duplicate row).
- An unknown kind is rejected at the schema layer (422).
- DELETE retracts a verdict (200); a second DELETE is 404 (nothing to retract).
- RBAC: a viewer (below member) is rejected with 403.
- Workspace isolation: workspace B's GET does not see workspace A's feedback.

DB-backed (Postgres) — skips cleanly when ``SIGNALS_TEST_DSN`` / ``DATABASE_DIRECT_URL``
/ ``DATABASE_URL`` are unset. Mirrors the G4 status-route test harness.
"""

from __future__ import annotations

import os
import types
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.ids import uuid7
from civicsignals_api.main import app as main_app
from civicsignals_api.modules.accounts.models import (
    Membership,
    MembershipRole,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.signals.models import Signal

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
_db_skip = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def _workspace(session: AsyncSession, *, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a workspace + owner user. Returns ``(workspace_id, user_id)``.

    The user id is real (a row in ``accounts_user``) so the feedback FK on ``user_id``
    is satisfied when the route's ctx writes feedback as this user.
    """
    user = User(id=uuid.uuid4(), email=email, password_hash="x", name="T")
    org = Organization(id=uuid.uuid4(), name="Org")
    session.add_all([user, org])
    await session.flush()
    ws = Workspace(
        id=uuid.uuid4(),
        organization_id=org.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:12]}",
        owner_id=user.id,
    )
    session.add(ws)
    await session.flush()
    return ws.id, user.id


async def _signal(session: AsyncSession) -> Signal:
    sig = Signal(
        id=uuid.uuid4(),
        entity_id=None,
        signal_type="rfp_posted",
        recipe_id="r",
        raw_document_ids=[],
        content_hash=uuid.uuid4().hex,
        observed_at=NOW,
        title="RFP: software",
        summary="RFP for software.",
        details={},
        confidence=0.9,
    )
    session.add(sig)
    await session.flush()
    return sig


def _make_ctx(
    ws_id: uuid.UUID,
    *,
    user_id: uuid.UUID | None = None,
    role: MembershipRole = MembershipRole.MEMBER,
) -> WorkspaceContext:
    uid = user_id or uuid.uuid4()
    user_ns = types.SimpleNamespace(id=uid, email="t@example.com")
    ws_ns = types.SimpleNamespace(id=ws_id)
    mem_ns = types.SimpleNamespace(id=uuid7(), workspace_id=ws_id, user_id=uid, role=role)
    return WorkspaceContext(
        user=cast(User, user_ns),
        workspace=cast(Workspace, ws_ns),
        membership=cast(Membership, mem_ns),
    )


def _client() -> tuple[httpx.AsyncClient, object]:
    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    main_app.dependency_overrides[get_session] = _override_session
    transport = httpx.ASGITransport(app=main_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return client, engine


@_db_skip
@pytest.mark.asyncio
async def test_post_records_and_detail_reflects_feedback(session: AsyncSession) -> None:
    ws, uid = await _workspace(session, email="fb-route-a@example.com")
    sig = await _signal(session)
    await session.commit()

    client, engine = _client()
    ctx = _make_ctx(ws, user_id=uid)
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        async with client:
            r = await client.post(
                f"/api/v1/signals/{sig.id}/feedback",
                headers={"X-Workspace-Id": str(ws)},
                json={"kind": "relevant"},
            )
            assert r.status_code == 200, r.text
            assert r.json()["kind"] == "relevant"

            # Change the verdict.
            r2 = await client.post(
                f"/api/v1/signals/{sig.id}/feedback",
                headers={"X-Workspace-Id": str(ws)},
                json={"kind": "not_relevant"},
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["kind"] == "not_relevant"

            # The detail view reflects the calling user's verdict.
            d = await client.get(
                f"/api/v1/signals/{sig.id}/detail",
                headers={"X-Workspace-Id": str(ws)},
            )
            assert d.status_code == 200, d.text
            assert d.json()["feedback"] == "not_relevant"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_unknown_kind_is_422(session: AsyncSession) -> None:
    ws, uid = await _workspace(session, email="fb-route-bad@example.com")
    sig = await _signal(session)
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws, user_id=uid)
    try:
        async with client:
            r = await client.post(
                f"/api/v1/signals/{sig.id}/feedback",
                headers={"X-Workspace-Id": str(ws)},
                json={"kind": "bogus"},
            )
        assert r.status_code == 422, r.text
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_delete_retracts_then_404(session: AsyncSession) -> None:
    ws, uid = await _workspace(session, email="fb-route-del@example.com")
    sig = await _signal(session)
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws, user_id=uid)
    try:
        async with client:
            await client.post(
                f"/api/v1/signals/{sig.id}/feedback",
                headers={"X-Workspace-Id": str(ws)},
                json={"kind": "relevant"},
            )
            r1 = await client.delete(
                f"/api/v1/signals/{sig.id}/feedback",
                headers={"X-Workspace-Id": str(ws)},
            )
            assert r1.status_code == 200, r1.text
            # Nothing left to retract → 404.
            r2 = await client.delete(
                f"/api/v1/signals/{sig.id}/feedback",
                headers={"X-Workspace-Id": str(ws)},
            )
            assert r2.status_code == 404, r2.text
            assert r2.headers["content-type"].startswith("application/problem+json")
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_viewer_is_forbidden(session: AsyncSession) -> None:
    ws, uid = await _workspace(session, email="fb-route-viewer@example.com")
    sig = await _signal(session)
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(
        ws, user_id=uid, role=MembershipRole.VIEWER
    )
    try:
        async with client:
            r = await client.post(
                f"/api/v1/signals/{sig.id}/feedback",
                headers={"X-Workspace-Id": str(ws)},
                json={"kind": "relevant"},
            )
        assert r.status_code == 403, r.text
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_feedback_is_workspace_isolated_in_detail(session: AsyncSession) -> None:
    ws_a, uid_a = await _workspace(session, email="fb-route-iso-a@example.com")
    ws_b, uid_b = await _workspace(session, email="fb-route-iso-b@example.com")
    sig = await _signal(session)
    await session.commit()

    client, engine = _client()
    ctx_a = _make_ctx(ws_a, user_id=uid_a)
    try:
        async with client:
            # A records feedback.
            main_app.dependency_overrides[require_workspace] = lambda: ctx_a
            await client.post(
                f"/api/v1/signals/{sig.id}/feedback",
                headers={"X-Workspace-Id": str(ws_a)},
                json={"kind": "relevant"},
            )
            # B's detail view sees no feedback for the same global signal.
            ctx_b = _make_ctx(ws_b, user_id=uid_b)
            main_app.dependency_overrides[require_workspace] = lambda: ctx_b
            d = await client.get(
                f"/api/v1/signals/{sig.id}/detail",
                headers={"X-Workspace-Id": str(ws_b)},
            )
            assert d.status_code == 200, d.text
            assert d.json()["feedback"] is None
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]
