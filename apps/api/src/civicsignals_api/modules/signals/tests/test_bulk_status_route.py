# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP route tests for the G3 bulk status endpoint (POST /signals/bulk-status).

Tests:
- Bulk pin / dismiss across several signals applies the transition to each row.
- Partial failure: a mix of valid + bad ids (no score row, illegal transition) is
  resilient — the valid moves apply and the bad ones are reported in ``skipped`` with a
  reason, rather than failing the whole batch.
- An idempotent no-op (a row already at the target) counts as succeeded.
- Batch-size cap: a request over MAX_BULK_STATUS_BATCH is a 422 and mutates nothing.
  An empty selection is a 422 at request validation.
- Workspace isolation: workspace B cannot transition workspace A's rows; A's rows are
  reported as ``not_in_workspace`` for B and left untouched.
- RBAC: a viewer (below member) is rejected with 403 and nothing is mutated.
- A successful bulk transition emits one SIGNAL_STATUS_CHANGED event per moved signal.

DB-backed (Postgres) — skips cleanly when ``SIGNALS_TEST_DSN`` /
``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` are unset (the WorkspaceScore table uses
Numeric + JSONB + TEXT[], which SQLite cannot replicate faithfully). Mirrors the G4
status-route test harness.
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
from sqlalchemy import select, text
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
    """Build a minimal WorkspaceContext stub for dependency override (role-aware)."""
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


async def _status_of(session: AsyncSession, workspace_id: uuid.UUID, signal_id: uuid.UUID) -> str:
    row = (
        await session.execute(
            select(WorkspaceScore)
            .where(WorkspaceScore.workspace_id == workspace_id)
            .where(WorkspaceScore.signal_id == signal_id)
        )
    ).scalar_one()
    await session.refresh(row)
    return row.status


# ---------------------------------------------------------------------------
# Tests — happy path (mass pin / dismiss)
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_bulk_dismiss_applies_to_all(session: AsyncSession) -> None:
    """Mass-dismiss across several new signals dismisses each row."""
    ws = await _workspace(session, email="bulk-dismiss@example.com")
    sigs = [await _signal(session, title=f"RFP {i}") for i in range(3)]
    for sig in sigs:
        await _score(session, workspace_id=ws, signal_id=sig.id, status="new")
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws)
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws)},
                json={"signal_ids": [str(s.id) for s in sigs], "status": "dismissed"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "dismissed"
        assert sorted(body["succeeded"]) == sorted(str(s.id) for s in sigs)
        assert body["skipped"] == []
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    for sig in sigs:
        assert await _status_of(session, ws, sig.id) == "dismissed"


@_db_skip
@pytest.mark.asyncio
async def test_bulk_pin_with_noop(session: AsyncSession) -> None:
    """Mass-pin pins new/reviewed rows; a row already pinned is an idempotent success."""
    ws = await _workspace(session, email="bulk-pin@example.com")
    s_new = await _signal(session, title="new one")
    s_reviewed = await _signal(session, title="reviewed one")
    s_pinned = await _signal(session, title="already pinned")
    await _score(session, workspace_id=ws, signal_id=s_new.id, status="new")
    await _score(session, workspace_id=ws, signal_id=s_reviewed.id, status="reviewed")
    await _score(session, workspace_id=ws, signal_id=s_pinned.id, status="pinned")
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws)
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws)},
                json={
                    "signal_ids": [str(s_new.id), str(s_reviewed.id), str(s_pinned.id)],
                    "status": "pinned",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # All three succeed — including the idempotent no-op on the already-pinned row.
        assert sorted(body["succeeded"]) == sorted(
            [str(s_new.id), str(s_reviewed.id), str(s_pinned.id)]
        )
        assert body["skipped"] == []
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    for sig in (s_new, s_reviewed, s_pinned):
        assert await _status_of(session, ws, sig.id) == "pinned"


# ---------------------------------------------------------------------------
# Tests — partial failure resilience
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_partial_failure_is_resilient(session: AsyncSession) -> None:
    """A mix of valid + bad ids applies the valid moves and reports the bad ones."""
    ws = await _workspace(session, email="bulk-partial@example.com")
    s_ok = await _signal(session, title="ok new")
    s_dismissed = await _signal(session, title="dismissed — illegal -> pinned")
    s_no_row = await _signal(session, title="no score row")
    await _score(session, workspace_id=ws, signal_id=s_ok.id, status="new")
    await _score(session, workspace_id=ws, signal_id=s_dismissed.id, status="dismissed")
    # s_no_row deliberately has no WorkspaceScore row.
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws)
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws)},
                json={
                    "signal_ids": [str(s_ok.id), str(s_dismissed.id), str(s_no_row.id)],
                    "status": "pinned",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == [str(s_ok.id)]
        skipped = {s["signal_id"]: s for s in body["skipped"]}
        assert skipped[str(s_dismissed.id)]["reason"] == "illegal_transition"
        assert skipped[str(s_dismissed.id)]["current"] == "dismissed"
        assert skipped[str(s_no_row.id)]["reason"] == "not_in_workspace"
        assert skipped[str(s_no_row.id)]["current"] is None
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    # The valid row moved; the illegal-transition row is unchanged.
    assert await _status_of(session, ws, s_ok.id) == "pinned"
    assert await _status_of(session, ws, s_dismissed.id) == "dismissed"


# ---------------------------------------------------------------------------
# Tests — batch-size cap / empty
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_batch_over_cap_is_422_and_mutates_nothing(session: AsyncSession) -> None:
    """A request over the batch cap is a 422 (schema) and nothing is transitioned."""
    from civicsignals_api.modules.signals import services as svc

    ws = await _workspace(session, email="bulk-cap@example.com")
    sig = await _signal(session)
    await _score(session, workspace_id=ws, signal_id=sig.id, status="new")
    await session.commit()

    # One real id + filler ids to exceed the cap.
    too_many = [str(sig.id)] + [str(uuid.uuid4()) for _ in range(svc.MAX_BULK_STATUS_BATCH)]

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws)
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws)},
                json={"signal_ids": too_many, "status": "dismissed"},
            )
        assert resp.status_code == 422, resp.text
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    # The real signal's row was not mutated.
    assert await _status_of(session, ws, sig.id) == "new"


@_db_skip
@pytest.mark.asyncio
async def test_empty_selection_is_422(session: AsyncSession) -> None:
    """An empty signal_ids list is rejected at request validation (min_length=1)."""
    ws = await _workspace(session, email="bulk-empty@example.com")
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws)
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws)},
                json={"signal_ids": [], "status": "dismissed"},
            )
        assert resp.status_code == 422, resp.text
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_pushed_is_not_settable_422(session: AsyncSession) -> None:
    """``pushed`` is not in the settable-status Literal → 422 at request validation."""
    ws = await _workspace(session, email="bulk-pushed@example.com")
    sig = await _signal(session)
    await _score(session, workspace_id=ws, signal_id=sig.id, status="new")
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws)
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws)},
                json={"signal_ids": [str(sig.id)], "status": "pushed"},
            )
        assert resp.status_code == 422, resp.text
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
    """Workspace B cannot transition workspace A's rows (reported skipped, untouched)."""
    ws_a = await _workspace(session, email="bulk-iso-a@example.com")
    ws_b = await _workspace(session, email="bulk-iso-b@example.com")
    sig = await _signal(session)
    score_a = await _score(session, workspace_id=ws_a, signal_id=sig.id, status="new")
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws_b)
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws_b)},
                json={"signal_ids": [str(sig.id)], "status": "dismissed"},
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["succeeded"] == []
        assert body["skipped"][0]["signal_id"] == str(sig.id)
        assert body["skipped"][0]["reason"] == "not_in_workspace"
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
    ws = await _workspace(session, email="bulk-viewer@example.com")
    sig = await _signal(session)
    score = await _score(session, workspace_id=ws, signal_id=sig.id, status="new")
    await session.commit()

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(
        ws, role=MembershipRole.VIEWER
    )
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws)},
                json={"signal_ids": [str(sig.id)], "status": "dismissed"},
            )
        assert resp.status_code == 403, resp.text
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    await session.refresh(score)
    assert score.status == "new"


# ---------------------------------------------------------------------------
# Tests — audit event
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_emits_one_event_per_moved_signal(session: AsyncSession) -> None:
    """A bulk transition emits one SIGNAL_STATUS_CHANGED per actually-moved signal.

    A signal already at the target status is an idempotent no-op: it counts as
    ``succeeded`` but is **not** moved, so it must emit no audit event (the route's
    docstring guarantees "one event per actually-transitioned signal"). The batch here
    mixes two real moves, one idempotent no-op, and one not-in-workspace signal — only
    the two real moves should produce an event.
    """
    ws = await _workspace(session, email="bulk-event@example.com")
    s1 = await _signal(session, title="moved 1")
    s2 = await _signal(session, title="moved 2")
    s_noop = await _signal(session, title="already dismissed (no-op)")
    s_no_row = await _signal(session, title="no row")
    await _score(session, workspace_id=ws, signal_id=s1.id, status="new")
    await _score(session, workspace_id=ws, signal_id=s2.id, status="new")
    # Already at the target — an idempotent no-op that must NOT emit an audit event.
    await _score(session, workspace_id=ws, signal_id=s_noop.id, status="dismissed")
    await session.commit()

    captured: list[dict[str, object]] = []

    async def _capture(payload: dict[str, object]) -> None:
        captured.append(payload)

    events.subscribe(events.SIGNAL_STATUS_CHANGED, _capture)

    client, engine = _client()
    main_app.dependency_overrides[require_workspace] = lambda: _make_ctx(ws)
    try:
        async with client:
            resp = await client.post(
                "/api/v1/signals/bulk-status",
                headers={"X-Workspace-Id": str(ws)},
                json={
                    "signal_ids": [
                        str(s1.id),
                        str(s2.id),
                        str(s_noop.id),
                        str(s_no_row.id),
                    ],
                    "status": "dismissed",
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The no-op row counts as succeeded (it's at the target) ...
        assert str(s_noop.id) in body["succeeded"]
    finally:
        events.unsubscribe(events.SIGNAL_STATUS_CHANGED, _capture)
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()  # type: ignore[attr-defined]

    # ... but only the two real moves emit an event — the no-op and the no-row signal
    # emit nothing.
    assert len(captured) == 2
    moved_ids = {str(s1.id), str(s2.id)}
    assert {c["signal_id"] for c in captured} == moved_ids
    assert str(s_noop.id) not in {c["signal_id"] for c in captured}
    assert all(c["status"] == "dismissed" for c in captured)
    assert all(c["workspace_id"] == str(ws) for c in captured)
