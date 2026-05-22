# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP route tests for the G4 status-transition endpoint (PATCH /signals/{id}/status).

Tests:
- A valid transition (new -> reviewed -> pinned) persists + returns the new status.
- A no-op re-set (status == current) is allowed (idempotent).
- Dismiss from any state, and restore (dismissed -> new), succeed.
- An illegal transition (new -> pushed via the schema, new -> ... jump) is rejected:
  - ``pushed`` is not a settable status → 422 at the request-validation layer.
  - a graph-illegal jump (pinned -> new) → 422 RFC 7807 problem from the service.
- No score row for the (workspace, signal) pair → 404.
- Workspace isolation: workspace B cannot transition workspace A's score row.
- RBAC: a viewer (below member) is rejected with 403.
- A successful transition emits the SIGNAL_STATUS_CHANGED audit event.

DB-backed (Postgres) — skips cleanly when ``SIGNALS_TEST_DSN`` /
``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` are unset (the WorkspaceScore table uses
Numeric + JSONB + TEXT[], which SQLite cannot replicate faithfully). Mirrors the G1
feed-route + G2 detail-route test harness.
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

from civicsignals_api import events
from civicsignals_api.db import Base, get_session
from civicsignals_api.ids import uuid7
from civicsignals_api.main import app as main_app
from civicsignals_api.modules.accounts.models import Membership, MembershipRole, User, Workspace
from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
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
    from civicsignals_api.modules.accounts.models import Organization

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
    return ws.id


async def _signal(session: AsyncSession, *, title: str = "RFP: software") -> Signal:
    sig = Signal(
        id=uuid.uuid4(),
        entity_id=None,
        signal_type="rfp_posted",
        recipe_id="r",
        raw_document_ids=[],
        content_hash=uuid.uuid4().hex,
        observed_at=NOW,
        title=title,
        summary="RFP for software.",
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
    status: str = "new",
) -> WorkspaceScore:
    ws_score = WorkspaceScore(
        id=uuid7(),
        workspace_id=workspace_id,
        signal_id=signal_id,
        score=70.0,
        score_breakdown={},
        status=status,
    )
    session.add(ws_score)
    await session.flush()
    return ws_score


# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    ws_id: uuid.UUID, *, role: MembershipRole = MembershipRole.MEMBER
) -> WorkspaceContext:
    """Build a minimal WorkspaceContext stub for dependency override.

    ``role`` defaults to MEMBER (the PATCH's floor); pass VIEWER to exercise the RBAC
    rejection. The membership's resolved role is what ``require_role`` checks.
    """
    user_ns = types.SimpleNamespace(id=uuid.uuid4(), email="t@example.com")
    ws_ns = types.SimpleNamespace(id=ws_id)
    mem_ns = types.SimpleNamespace(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_ns.id,
        role=role,
    )
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


# ---------------------------------------------------------------------------
# Tests — valid transitions
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_valid_transition_new_to_reviewed_to_pinned(session: AsyncSession) -> None:
    """A member can move new -> reviewed -> pinned; each step persists."""
    ws = await _workspace(session, email="status-valid@example.com")
    sig = await _signal(session)
    await _score(session, workspace_id=ws, signal_id=sig.id, status="new")
    await session.commit()

    client, engine = _client()
    ctx = _make_ctx(ws)
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        async with client:
            r1 = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "reviewed"},
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["status"] == "reviewed"
            assert r1.json()["signal_id"] == str(sig.id)

            r2 = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "pinned"},
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["status"] == "pinned"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    # Persisted to the row.
    refreshed = await session.get(WorkspaceScore, (await _only_score(session, ws, sig.id)).id)
    assert refreshed is not None
    await session.refresh(refreshed)
    assert refreshed.status == "pinned"


@_db_skip
@pytest.mark.asyncio
async def test_noop_reset_is_allowed(session: AsyncSession) -> None:
    """Re-setting the current status is an idempotent no-op (200)."""
    ws = await _workspace(session, email="status-noop@example.com")
    sig = await _signal(session)
    await _score(session, workspace_id=ws, signal_id=sig.id, status="reviewed")
    await session.commit()

    client, engine = _client()
    ctx = _make_ctx(ws)
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        async with client:
            resp = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "reviewed"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "reviewed"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_dismiss_then_restore(session: AsyncSession) -> None:
    """Any state can be dismissed; a dismissed row restores to new."""
    ws = await _workspace(session, email="status-dismiss@example.com")
    sig = await _signal(session)
    await _score(session, workspace_id=ws, signal_id=sig.id, status="pinned")
    await session.commit()

    client, engine = _client()
    ctx = _make_ctx(ws)
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        async with client:
            r1 = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "dismissed"},
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["status"] == "dismissed"

            r2 = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "new"},
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["status"] == "new"
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests — illegal transitions / errors
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_pushed_is_not_settable_422(session: AsyncSession) -> None:
    """``pushed`` is not in the settable-status Literal → 422 at request validation."""
    ws = await _workspace(session, email="status-pushed@example.com")
    sig = await _signal(session)
    await _score(session, workspace_id=ws, signal_id=sig.id, status="new")
    await session.commit()

    client, engine = _client()
    ctx = _make_ctx(ws)
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        async with client:
            resp = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "pushed"},
            )
        assert resp.status_code == 422, resp.text
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_illegal_graph_jump_returns_422_problem(session: AsyncSession) -> None:
    """A graph-illegal transition (pinned -> new) returns an RFC 7807 422 problem."""
    ws = await _workspace(session, email="status-jump@example.com")
    sig = await _signal(session)
    await _score(session, workspace_id=ws, signal_id=sig.id, status="pinned")
    await session.commit()

    client, engine = _client()
    ctx = _make_ctx(ws)
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        async with client:
            resp = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "new"},
            )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["status"] == 422
        assert "transition" in body["title"].lower()
        # The detail names the current state + allowed targets.
        assert "pinned" in body["detail"]
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_no_score_row_returns_404(session: AsyncSession) -> None:
    """A signal with no score row in the workspace → 404 (nothing to transition)."""
    ws = await _workspace(session, email="status-404@example.com")
    sig = await _signal(session)  # no score row created
    await session.commit()

    client, engine = _client()
    ctx = _make_ctx(ws)
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        async with client:
            resp = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "reviewed"},
            )
        assert resp.status_code == 404, resp.text
        assert resp.json()["status"] == 404
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tests — workspace isolation + RBAC
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_workspace_isolation(session: AsyncSession) -> None:
    """Workspace B cannot transition workspace A's score row (404, not mutated)."""
    ws_a = await _workspace(session, email="status-iso-a@example.com")
    ws_b = await _workspace(session, email="status-iso-b@example.com")
    sig = await _signal(session)
    # Only ws_a has a score row for this signal.
    score_a = await _score(session, workspace_id=ws_a, signal_id=sig.id, status="new")
    await session.commit()

    client, engine = _client()
    try:
        async with client:
            # Workspace B (no row for this signal) attempts the transition → 404.
            ctx_b = _make_ctx(ws_b)
            main_app.dependency_overrides[require_workspace] = lambda: ctx_b
            resp_b = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws_b)},
                json={"status": "reviewed"},
            )
            assert resp_b.status_code == 404, resp_b.text
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    # Workspace A's row is untouched.
    await session.refresh(score_a)
    assert score_a.status == "new"


@_db_skip
@pytest.mark.asyncio
async def test_viewer_is_forbidden_403(session: AsyncSession) -> None:
    """A viewer (below the member floor) is rejected with 403 (RBAC, B7)."""
    ws = await _workspace(session, email="status-viewer@example.com")
    sig = await _signal(session)
    score = await _score(session, workspace_id=ws, signal_id=sig.id, status="new")
    await session.commit()

    client, engine = _client()
    # NOTE: we do NOT override require_workspace here — we override it with a VIEWER
    # ctx so the require_role(MEMBER) dependency runs against a real role and rejects.
    ctx_viewer = _make_ctx(ws, role=MembershipRole.VIEWER)
    main_app.dependency_overrides[require_workspace] = lambda: ctx_viewer
    try:
        async with client:
            resp = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "reviewed"},
            )
        assert resp.status_code == 403, resp.text
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    # The row was not mutated.
    await session.refresh(score)
    assert score.status == "new"


# ---------------------------------------------------------------------------
# Tests — audit event
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_emits_status_changed_event(session: AsyncSession) -> None:
    """A successful transition publishes SIGNAL_STATUS_CHANGED with the new status."""
    ws = await _workspace(session, email="status-event@example.com")
    sig = await _signal(session)
    await _score(session, workspace_id=ws, signal_id=sig.id, status="new")
    await session.commit()

    captured: list[dict[str, object]] = []

    async def _capture(payload: dict[str, object]) -> None:
        captured.append(payload)

    events.subscribe(events.SIGNAL_STATUS_CHANGED, _capture)

    client, engine = _client()
    ctx = _make_ctx(ws)
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    try:
        async with client:
            resp = await client.patch(
                f"/api/v1/signals/{sig.id}/status",
                headers={"X-Workspace-Id": str(ws)},
                json={"status": "reviewed"},
            )
        assert resp.status_code == 200, resp.text
    finally:
        events.unsubscribe(events.SIGNAL_STATUS_CHANGED, _capture)
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    assert len(captured) == 1
    assert captured[0]["signal_id"] == str(sig.id)
    assert captured[0]["workspace_id"] == str(ws)
    assert captured[0]["status"] == "reviewed"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _only_score(
    session: AsyncSession, workspace_id: uuid.UUID, signal_id: uuid.UUID
) -> WorkspaceScore:
    from sqlalchemy import select

    row = (
        await session.execute(
            select(WorkspaceScore)
            .where(WorkspaceScore.workspace_id == workspace_id)
            .where(WorkspaceScore.signal_id == signal_id)
        )
    ).scalar_one()
    return row
