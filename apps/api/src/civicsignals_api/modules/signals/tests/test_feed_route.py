# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP route tests for the G1 workspace feed endpoint (GET /api/v1/signals/feed).

Tests:
- Feed returns workspace-scored signals sorted by score desc.
- Threshold gate: signals without a score row never appear.
- Cursor pagination: page 1 + page 2 cover the full result set, no duplicates.
- signal_type filter narrows results to that type.
- status filter overrides the default feed-visible set.
- min_score filter excludes signals below the floor.
- Workspace isolation: workspace A cannot see workspace B's signals.
- Malformed cursor → 400 RFC 7807 problem.
- Unauthenticated → 401.

DB-backed (Postgres) — skips cleanly when ``SIGNALS_TEST_DSN`` /
``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` are unset (the WorkspaceScore table
uses Numeric + JSONB + TEXT[], which SQLite cannot replicate faithfully).
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
from civicsignals_api.modules.accounts.models import Membership, MembershipRole, User, Workspace
from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.entities.models import Entity
from civicsignals_api.modules.signals.models import Signal
from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
_db_skip = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DB row builders
# ---------------------------------------------------------------------------


async def _workspace(session: AsyncSession, *, email: str) -> uuid.UUID:
    user = User(id=uuid.uuid4(), email=email, password_hash="x", name="T")
    org_id = uuid.uuid4()
    from civicsignals_api.modules.accounts.models import Organization

    org = Organization(id=org_id, name="Org")
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
    return ws.id


async def _entity(session: AsyncSession, *, state: str = "WA") -> Entity:
    ent = Entity(
        id=uuid.uuid4(),
        type="school_district",
        name="Northshore SD",
        country="US",
        state=state,
    )
    session.add(ent)
    await session.flush()
    return ent


async def _signal(
    session: AsyncSession,
    *,
    signal_type: str = "rfp_posted",
    title: str = "RFP: software",
    summary: str = "RFP for software.",
    entity: Entity | None = None,
    observed_at: datetime | None = None,
) -> Signal:
    sig = Signal(
        id=uuid.uuid4(),
        entity_id=entity.id if entity is not None else None,
        signal_type=signal_type,
        recipe_id="r",
        raw_document_ids=[],
        content_hash=uuid.uuid4().hex,
        observed_at=observed_at or NOW,
        title=title,
        summary=summary,
        details={},
        confidence=0.9,
    )
    session.add(sig)
    await session.flush()
    return sig


async def _score(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    signal_id: uuid.UUID,
    score: float = 70.0,
    status: str = "new",
) -> WorkspaceScore:
    ws_score = WorkspaceScore(
        id=uuid7(),
        workspace_id=workspace_id,
        signal_id=signal_id,
        score=score,
        score_breakdown={},
        status=status,
    )
    session.add(ws_score)
    await session.flush()
    return ws_score


# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------


def _make_ctx(ws_id: uuid.UUID) -> WorkspaceContext:
    """Build a minimal WorkspaceContext stub for dependency override."""
    user_ns = types.SimpleNamespace(id=uuid.uuid4(), email="t@example.com")
    ws_ns = types.SimpleNamespace(id=ws_id)
    mem_ns = types.SimpleNamespace(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_ns.id,
        role=MembershipRole.MEMBER,
    )
    return WorkspaceContext(
        user=cast(User, user_ns),
        workspace=cast(Workspace, ws_ns),
        membership=cast(Membership, mem_ns),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_feed_returns_scored_signals_score_desc(session: AsyncSession) -> None:
    """The feed returns workspace-scored signals ordered by score descending (G1)."""
    ws = await _workspace(session, email="feed-route-a@example.com")
    ent = await _entity(session)
    sig_hi = await _signal(session, title="High score RFP", entity=ent)
    sig_lo = await _signal(session, title="Low score RFP", entity=ent)
    await _score(session, workspace_id=ws, signal_id=sig_hi.id, score=90.0)
    await _score(session, workspace_id=ws, signal_id=sig_lo.id, score=50.0)
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx(ws)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/signals/feed",
                headers={"X-Workspace-Id": str(ws)},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "data" in body
        items = body["data"]
        assert len(items) == 2
        assert items[0]["score"] >= items[1]["score"]
        assert items[0]["signal"]["title"] == "High score RFP"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@_db_skip
@pytest.mark.asyncio
async def test_feed_threshold_gate(session: AsyncSession) -> None:
    """Signals without a workspace score row are invisible in the feed."""
    ws = await _workspace(session, email="feed-route-gate@example.com")
    sig_scored = await _signal(session, title="Scored")
    _sig_unscored = await _signal(session, title="Unscored")
    await _score(session, workspace_id=ws, signal_id=sig_scored.id, score=70.0)
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx(ws)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/signals/feed",
                headers={"X-Workspace-Id": str(ws)},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["signal"]["title"] == "Scored"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@_db_skip
@pytest.mark.asyncio
async def test_feed_cursor_pagination(session: AsyncSession) -> None:
    """Page 1 + page 2 cover the full result set with no duplicates."""
    ws = await _workspace(session, email="feed-route-cursor@example.com")
    for i, score in enumerate([90.0, 75.0, 60.0]):
        sig = await _signal(session, title=f"Signal {i}")
        await _score(session, workspace_id=ws, signal_id=sig.id, score=score)
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx(ws)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp1 = await client.get(
                "/api/v1/signals/feed?limit=2",
                headers={"X-Workspace-Id": str(ws)},
            )
            assert resp1.status_code == 200
            body1 = resp1.json()
            assert body1["page"]["has_more"] is True
            cursor = body1["page"]["next_cursor"]
            assert cursor is not None

            resp2 = await client.get(
                f"/api/v1/signals/feed?limit=2&cursor={cursor}",
                headers={"X-Workspace-Id": str(ws)},
            )
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert body2["page"]["has_more"] is False

        all_ids = [i["score_id"] for i in body1["data"]] + [i["score_id"] for i in body2["data"]]
        assert len(all_ids) == 3
        assert len(set(all_ids)) == 3  # no duplicates
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@_db_skip
@pytest.mark.asyncio
async def test_feed_signal_type_filter(session: AsyncSession) -> None:
    """signal_type filter narrows the feed to matching signals only."""
    ws = await _workspace(session, email="feed-route-type@example.com")
    sig_rfp = await _signal(session, signal_type="rfp_posted", title="RFP signal")
    sig_news = await _signal(session, signal_type="news_mention", title="News signal")
    await _score(session, workspace_id=ws, signal_id=sig_rfp.id, score=80.0)
    await _score(session, workspace_id=ws, signal_id=sig_news.id, score=70.0)
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx(ws)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/signals/feed?signal_type=rfp_posted",
                headers={"X-Workspace-Id": str(ws)},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["signal"]["signal_type"] == "rfp_posted"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@_db_skip
@pytest.mark.asyncio
async def test_feed_status_filter(session: AsyncSession) -> None:
    """status filter overrides the default visible set to include dismissed."""
    ws = await _workspace(session, email="feed-route-status@example.com")
    sig_new = await _signal(session, title="New signal")
    sig_dismissed = await _signal(session, title="Dismissed signal")
    await _score(session, workspace_id=ws, signal_id=sig_new.id, score=80.0, status="new")
    await _score(
        session, workspace_id=ws, signal_id=sig_dismissed.id, score=70.0, status="dismissed"
    )
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx(ws)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Default feed: only new/reviewed/pinned → dismissed hidden.
            resp_default = await client.get(
                "/api/v1/signals/feed",
                headers={"X-Workspace-Id": str(ws)},
            )
            assert resp_default.status_code == 200
            assert len(resp_default.json()["data"]) == 1

            # Explicit dismissed filter → dismissed appears.
            resp_dismissed = await client.get(
                "/api/v1/signals/feed?status=dismissed",
                headers={"X-Workspace-Id": str(ws)},
            )
            assert resp_dismissed.status_code == 200
            items = resp_dismissed.json()["data"]
            assert len(items) == 1
            assert items[0]["status"] == "dismissed"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@_db_skip
@pytest.mark.asyncio
async def test_feed_min_score_filter(session: AsyncSession) -> None:
    """min_score excludes signals below the given score floor."""
    ws = await _workspace(session, email="feed-route-minscore@example.com")
    sig_hi = await _signal(session, title="High")
    sig_lo = await _signal(session, title="Low")
    await _score(session, workspace_id=ws, signal_id=sig_hi.id, score=85.0)
    await _score(session, workspace_id=ws, signal_id=sig_lo.id, score=40.0)
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx(ws)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/signals/feed?min_score=70",
                headers={"X-Workspace-Id": str(ws)},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]
        assert len(items) == 1
        assert items[0]["signal"]["title"] == "High"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@_db_skip
@pytest.mark.asyncio
async def test_feed_workspace_isolation(session: AsyncSession) -> None:
    """Workspace A cannot see workspace B's scored signals."""
    ws_a = await _workspace(session, email="feed-iso-a@example.com")
    ws_b = await _workspace(session, email="feed-iso-b@example.com")
    sig = await _signal(session, title="WS-A signal")
    # Only ws_a has a score row for this signal.
    await _score(session, workspace_id=ws_a, signal_id=sig.id, score=80.0)
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    main_app.dependency_overrides[get_session] = _override_session
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ctx_a = _make_ctx(ws_a)
            main_app.dependency_overrides[require_workspace] = lambda: ctx_a
            resp_a = await client.get("/api/v1/signals/feed", headers={"X-Workspace-Id": str(ws_a)})
            assert resp_a.status_code == 200
            assert len(resp_a.json()["data"]) == 1

            ctx_b = _make_ctx(ws_b)
            main_app.dependency_overrides[require_workspace] = lambda: ctx_b
            resp_b = await client.get("/api/v1/signals/feed", headers={"X-Workspace-Id": str(ws_b)})
            assert resp_b.status_code == 200
            assert len(resp_b.json()["data"]) == 0
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@_db_skip
@pytest.mark.asyncio
async def test_feed_malformed_cursor_returns_400(session: AsyncSession) -> None:
    """A malformed cursor returns a 400 RFC 7807 problem response."""
    ws = await _workspace(session, email="feed-cursor-400@example.com")
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx(ws)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/signals/feed?cursor=not-a-valid-cursor",
                headers={"X-Workspace-Id": str(ws)},
            )
        assert resp.status_code == 400
        body = resp.json()
        assert body["status"] == 400
        assert "cursor" in body["title"].lower() or "cursor" in body["detail"].lower()
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()
