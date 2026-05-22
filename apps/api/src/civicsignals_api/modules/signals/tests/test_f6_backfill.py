# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for F6: ICP-change workspace backfill (doc 14 §7).

Two sets of tests:

* **Pure-Python / mock tests** (no Postgres needed): verify the listener's
  sync + async split, task enqueue, lock-based concurrency cap, and workspace
  isolation without touching the DB.

* **DB-backed integration tests** (skip when no DSN): end-to-end flow through
  ``candidate_signal_ids_for_icp`` → ``score_workspace_candidates`` (idempotency,
  threshold gating, workspace isolation) and the full ``_on_icp_changed`` path.

All Celery calls are mocked via ``patch`` so the tests remain unit-safe and fast.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Helpers / fixtures shared across both test groups
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
_skip_no_db = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")


# ---------------------------------------------------------------------------
# Pure-Python tests (no DB required)
# ---------------------------------------------------------------------------


class TestListenerSyncAsyncSplit:
    """Verify the two-phase ICP-changed handler: sync score + async task enqueue."""

    @pytest.mark.asyncio
    async def test_on_icp_changed_enqueues_task_after_sync(self) -> None:
        """_on_icp_changed runs a sync score pass then enqueues rescore_workspace."""
        from civicsignals_api.modules.signals.listeners import _on_icp_changed

        ws_id = str(uuid.uuid4())

        fake_icp = MagicMock()
        fake_icp.workspace_id = uuid.UUID(ws_id)
        fake_icp.signal_types = ["rfp_posted"]
        fake_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "civicsignals_api.modules.signals.listeners.SessionLocal",
                return_value=mock_sess,
            ),
            patch(
                "civicsignals_api.modules.signals.listeners.services.candidate_signal_ids_for_icp",
                new=AsyncMock(return_value=fake_ids),
            ),
            patch(
                "civicsignals_api.modules.signals.listeners.services.score_workspace_candidates",
                new=AsyncMock(return_value=3),
            ),
            patch("civicsignals_api.modules.signals.tasks.rescore_workspace") as mock_task,
            patch(
                "civicsignals_api.modules.icp.services.get_active_icp",
                new=AsyncMock(return_value=fake_icp),
            ),
        ):
            await _on_icp_changed({"workspace_id": ws_id})

        mock_task.delay.assert_called_once_with(ws_id)

    @pytest.mark.asyncio
    async def test_on_icp_changed_bad_workspace_id_is_swallowed(self) -> None:
        """A malformed workspace_id in the event payload is logged+swallowed (no crash)."""
        from civicsignals_api.modules.signals.listeners import _on_icp_changed

        await _on_icp_changed({"workspace_id": "not-a-uuid"})

    @pytest.mark.asyncio
    async def test_on_icp_changed_missing_workspace_id_is_swallowed(self) -> None:
        """A missing workspace_id is swallowed gracefully."""
        from civicsignals_api.modules.signals.listeners import _on_icp_changed

        await _on_icp_changed({})

    @pytest.mark.asyncio
    async def test_on_icp_changed_always_enqueues_task(self) -> None:
        """_on_icp_changed enqueues the Celery task even when no active ICP exists."""
        from civicsignals_api.modules.signals.listeners import _on_icp_changed

        ws_id = str(uuid.uuid4())

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "civicsignals_api.modules.signals.listeners.SessionLocal",
                return_value=mock_sess,
            ),
            patch(
                "civicsignals_api.modules.icp.services.get_active_icp",
                new=AsyncMock(return_value=None),
            ),
            patch("civicsignals_api.modules.signals.tasks.rescore_workspace") as mock_task,
        ):
            await _on_icp_changed({"workspace_id": ws_id})

        # Even with no active ICP the task is enqueued (it handles the no-ICP case).
        mock_task.delay.assert_called_once_with(ws_id)

    def test_register_and_unregister_listeners(self) -> None:
        """register_listeners/unregister_listeners are symmetric."""
        from civicsignals_api import events
        from civicsignals_api.modules.signals.listeners import (
            _on_icp_changed,
            _on_signal_created,
            register_listeners,
            unregister_listeners,
        )

        unregister_listeners()

        register_listeners()
        assert _on_signal_created in events._subscribers.get(events.SIGNAL_CREATED, [])
        assert _on_icp_changed in events._subscribers.get(events.ICP_CHANGED, [])

        unregister_listeners()
        assert _on_signal_created not in events._subscribers.get(events.SIGNAL_CREATED, [])
        assert _on_icp_changed not in events._subscribers.get(events.ICP_CHANGED, [])


class TestRescoreWorkspaceTask:
    """Unit tests for the Celery rescore_workspace task (mock DB + Redis)."""

    def test_bad_workspace_id_returns_zero(self) -> None:
        """A malformed workspace_id is swallowed and returns 0."""
        from civicsignals_api.modules.signals.tasks import _rescore_workspace_async

        result = asyncio.run(_rescore_workspace_async("not-a-uuid"))
        assert result == 0

    def test_lock_held_returns_zero(self) -> None:
        """If the per-workspace lock is already held, skip + return 0."""
        from civicsignals_api.modules.signals.tasks import _rescore_workspace_async

        ws_id = str(uuid.uuid4())
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False

        with (
            patch(
                "civicsignals_api.modules.signals.tasks.RedisLock",
                return_value=mock_lock,
            ),
            patch("civicsignals_api.modules.signals.tasks.get_redis_client"),
        ):
            result = asyncio.run(_rescore_workspace_async(ws_id))

        assert result == 0
        mock_lock.release.assert_not_called()

    def test_no_active_icp_returns_zero_and_releases_lock(self) -> None:
        """No active ICP → 0 written, lock always released."""
        from civicsignals_api.modules.signals.tasks import _rescore_workspace_async

        ws_id = str(uuid.uuid4())
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "civicsignals_api.modules.signals.tasks.RedisLock",
                return_value=mock_lock,
            ),
            patch("civicsignals_api.modules.signals.tasks.get_redis_client"),
            patch(
                "civicsignals_api.modules.signals.tasks.SessionLocal",
                return_value=mock_sess,
            ),
            patch(
                "civicsignals_api.modules.icp.services.get_active_icp",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = asyncio.run(_rescore_workspace_async(ws_id))

        assert result == 0
        mock_lock.release.assert_called_once()

    def test_lock_released_on_exception(self) -> None:
        """The lock is released even when an unexpected error occurs mid-backfill."""
        from civicsignals_api.modules.signals.tasks import _rescore_workspace_async

        ws_id = str(uuid.uuid4())
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        fake_icp = MagicMock()
        fake_icp.signal_types = []

        class _CrashingSession:
            async def __aenter__(self) -> _CrashingSession:
                return self

            async def __aexit__(self, *a: Any) -> bool:
                return False

            async def commit(self) -> None:
                pass

        with (
            patch(
                "civicsignals_api.modules.signals.tasks.RedisLock",
                return_value=mock_lock,
            ),
            patch("civicsignals_api.modules.signals.tasks.get_redis_client"),
            patch(
                "civicsignals_api.modules.signals.tasks.SessionLocal",
                side_effect=lambda: _CrashingSession(),
            ),
            patch(
                "civicsignals_api.modules.icp.services.get_active_icp",
                new=AsyncMock(return_value=fake_icp),
            ),
            patch(
                "civicsignals_api.modules.signals.services.candidate_signal_ids_for_icp",
                new=AsyncMock(side_effect=RuntimeError("db exploded")),
            ),
            pytest.raises(RuntimeError, match="db exploded"),
        ):
            asyncio.run(_rescore_workspace_async(ws_id))

        mock_lock.release.assert_called_once()

    def test_batches_pages_through_remainder(self) -> None:
        """The task pages through remaining candidates in batches after the sync offset."""
        from civicsignals_api.modules.signals import services as sig_services
        from civicsignals_api.modules.signals.tasks import _rescore_workspace_async

        ws_id = str(uuid.uuid4())
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = True

        fake_icp = MagicMock()
        fake_icp.signal_types = ["rfp_posted"]

        batch_size = sig_services.BACKFILL_BATCH_SIZE
        batch_calls: list[list[uuid.UUID]] = [
            [uuid.uuid4() for _ in range(batch_size)],
            [uuid.uuid4()],
            [],
        ]
        call_idx = 0

        async def fake_candidate_ids(session: Any, icp: Any, **kwargs: Any) -> list[uuid.UUID]:
            nonlocal call_idx
            ids = batch_calls[call_idx]
            call_idx += 1
            return ids

        async def fake_score(
            session: Any,
            *,
            workspace_id: Any,
            signal_ids: Any,
            **kwargs: Any,
        ) -> int:
            return len(signal_ids)

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_sess.commit = AsyncMock()

        with (
            patch(
                "civicsignals_api.modules.signals.tasks.RedisLock",
                return_value=mock_lock,
            ),
            patch("civicsignals_api.modules.signals.tasks.get_redis_client"),
            patch(
                "civicsignals_api.modules.signals.tasks.SessionLocal",
                return_value=mock_sess,
            ),
            patch(
                "civicsignals_api.modules.icp.services.get_active_icp",
                new=AsyncMock(return_value=fake_icp),
            ),
            patch(
                "civicsignals_api.modules.signals.services.candidate_signal_ids_for_icp",
                new=fake_candidate_ids,
            ),
            patch(
                "civicsignals_api.modules.signals.services.score_workspace_candidates",
                new=fake_score,
            ),
        ):
            total = asyncio.run(_rescore_workspace_async(ws_id))

        assert total == batch_size + 1
        mock_lock.release.assert_called_once()


class TestIcpServiceEmitsEvent:
    """Verify that icp.services emits ICP_CHANGED on create / update / activate."""

    @pytest.mark.asyncio
    async def test_create_icp_publishes_event_when_active(self) -> None:
        """create_icp publishes ICP_CHANGED when is_active=True."""
        from civicsignals_api import events

        published: list[dict[str, object]] = []

        async def capture(payload: dict[str, object]) -> None:
            published.append(payload)

        events.subscribe(events.ICP_CHANGED, capture)
        try:
            fake_icp = MagicMock()
            fake_icp.is_active = True
            fake_icp.workspace_id = uuid.uuid4()
            fake_icp.id = uuid.uuid4()

            from sqlalchemy.ext.asyncio import AsyncSession

            mock_session = AsyncMock(spec=AsyncSession)
            mock_session.flush = AsyncMock()
            mock_session.refresh = AsyncMock()
            mock_session.add = MagicMock()

            with (
                patch("civicsignals_api.modules.icp.services._validate_ranges"),
                patch(
                    "civicsignals_api.modules.icp.services._deactivate_others",
                    new=AsyncMock(),
                ),
                patch(
                    "civicsignals_api.modules.icp.services.IcpDefinition",
                    return_value=fake_icp,
                ),
            ):
                from civicsignals_api.modules.icp import services as icp_svc

                await icp_svc.create_icp(
                    mock_session,
                    workspace_id=fake_icp.workspace_id,
                    name="Test",
                    is_active=True,
                )

            assert len(published) == 1
            assert published[0]["workspace_id"] == str(fake_icp.workspace_id)
        finally:
            events.unsubscribe(events.ICP_CHANGED, capture)

    @pytest.mark.asyncio
    async def test_create_icp_does_not_publish_when_inactive(self) -> None:
        """create_icp does NOT publish ICP_CHANGED when is_active=False."""
        from civicsignals_api import events

        published: list[object] = []

        async def capture(payload: dict[str, object]) -> None:
            published.append(payload)

        events.subscribe(events.ICP_CHANGED, capture)
        try:
            fake_icp = MagicMock()
            fake_icp.is_active = False
            fake_icp.workspace_id = uuid.uuid4()
            fake_icp.id = uuid.uuid4()

            from sqlalchemy.ext.asyncio import AsyncSession

            mock_session = AsyncMock(spec=AsyncSession)
            mock_session.flush = AsyncMock()
            mock_session.refresh = AsyncMock()
            mock_session.add = MagicMock()

            with (
                patch("civicsignals_api.modules.icp.services._validate_ranges"),
                patch(
                    "civicsignals_api.modules.icp.services.IcpDefinition",
                    return_value=fake_icp,
                ),
            ):
                from civicsignals_api.modules.icp import services as icp_svc

                await icp_svc.create_icp(
                    mock_session,
                    workspace_id=fake_icp.workspace_id,
                    name="Draft",
                    is_active=False,
                )

            assert published == []
        finally:
            events.unsubscribe(events.ICP_CHANGED, capture)


# ---------------------------------------------------------------------------
# DB-backed integration tests (skip when no Postgres DSN)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[Any]:
    """Full-schema Postgres session for F6 integration tests."""
    if _DSN is None:
        pytest.skip("no Postgres DSN configured")
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.pool import NullPool

    from civicsignals_api.db import Base

    # Import all models so Base.metadata is complete.
    from civicsignals_api.modules.accounts.models import (  # noqa: F401
        Membership,
        Organization,
        User,
        Workspace,
    )
    from civicsignals_api.modules.entities import models as _ent  # noqa: F401
    from civicsignals_api.modules.entities.models import Entity  # noqa: F401
    from civicsignals_api.modules.icp.models import IcpDefinition  # noqa: F401

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


async def _make_workspace(session: Any, *, email: str) -> uuid.UUID:
    from civicsignals_api.modules.accounts.models import Organization, User, Workspace

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
    return ws.id


async def _make_icp(
    session: Any,
    *,
    workspace_id: uuid.UUID,
    signal_types: list[str] | None = None,
    threshold: int = 30,
) -> Any:
    from civicsignals_api.modules.icp.models import IcpDefinition

    icp = IcpDefinition(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Test ICP",
        countries=["US"],
        states=[],
        entity_kinds=[],
        signal_types=signal_types if signal_types is not None else ["rfp_posted"],
        signal_weights={"rfp_posted": 1.0},
        keywords_required=[],
        keywords_excluded=[],
        threshold=threshold,
        is_active=True,
    )
    session.add(icp)
    await session.flush()
    return icp


async def _make_signal(
    session: Any,
    *,
    signal_type: str = "rfp_posted",
    observed_at: datetime | None = None,
    confidence: float | None = 0.9,
) -> Any:
    from civicsignals_api.modules.signals.models import Signal

    sig = Signal(
        id=uuid.uuid4(),
        signal_type=signal_type,
        recipe_id="r",
        raw_document_ids=[],
        content_hash=uuid.uuid4().hex,
        observed_at=observed_at or NOW,
        title="RFP: test signal",
        summary="A test RFP signal for F6 backfill testing.",
        details={},
        confidence=confidence,
    )
    session.add(sig)
    await session.flush()
    return sig


@pytest.mark.asyncio
@_skip_no_db
async def test_candidate_signal_ids_respects_signal_type_filter(db_session: Any) -> None:
    """candidate_signal_ids_for_icp returns only signals matching the ICP signal type."""
    from civicsignals_api.modules.signals import services

    ws = await _make_workspace(db_session, email="cand-filter@example.com")
    icp = await _make_icp(db_session, workspace_id=ws, signal_types=["rfp_posted"])
    await _make_signal(db_session, signal_type="rfp_posted")
    await _make_signal(db_session, signal_type="grant_awarded")
    await db_session.commit()

    ids = await services.candidate_signal_ids_for_icp(db_session, icp)
    assert len(ids) == 1


@pytest.mark.asyncio
@_skip_no_db
async def test_candidate_signal_ids_respects_lookback_window(db_session: Any) -> None:
    """Signals older than BACKFILL_LOOKBACK_DAYS are excluded."""
    from civicsignals_api.modules.signals import services

    ws = await _make_workspace(db_session, email="lookback@example.com")
    icp = await _make_icp(db_session, workspace_id=ws, signal_types=["rfp_posted"])

    old = NOW - timedelta(days=services.BACKFILL_LOOKBACK_DAYS + 10)
    recent = NOW - timedelta(days=10)

    await _make_signal(db_session, signal_type="rfp_posted", observed_at=old)
    sig_recent = await _make_signal(db_session, signal_type="rfp_posted", observed_at=recent)
    await db_session.commit()

    ids = await services.candidate_signal_ids_for_icp(db_session, icp)
    assert sig_recent.id in ids
    assert len(ids) == 1


@pytest.mark.asyncio
@_skip_no_db
async def test_score_workspace_candidates_idempotent(db_session: Any) -> None:
    """Running score_workspace_candidates twice yields the same rows (no dupes)."""
    from sqlalchemy import select

    from civicsignals_api.modules.signals import services
    from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore

    ws = await _make_workspace(db_session, email="idem@example.com")
    icp = await _make_icp(db_session, workspace_id=ws, signal_types=["rfp_posted"], threshold=30)
    sig = await _make_signal(db_session, signal_type="rfp_posted")
    await db_session.commit()

    await services.score_workspace_candidates(
        db_session, workspace_id=ws, signal_ids=[sig.id], icp=icp
    )
    await db_session.commit()

    await services.score_workspace_candidates(
        db_session, workspace_id=ws, signal_ids=[sig.id], icp=icp
    )
    await db_session.commit()

    rows = list(
        (await db_session.execute(select(WorkspaceScore).where(WorkspaceScore.workspace_id == ws)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].signal_id == sig.id


@pytest.mark.asyncio
@_skip_no_db
async def test_score_workspace_candidates_threshold_gating(db_session: Any) -> None:
    """A signal that doesn't clear the threshold leaves no score row."""
    from sqlalchemy import select

    from civicsignals_api.modules.signals import services
    from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore

    ws = await _make_workspace(db_session, email="thresh@example.com")
    icp = await _make_icp(db_session, workspace_id=ws, signal_types=["rfp_posted"], threshold=100)
    sig = await _make_signal(db_session, signal_type="rfp_posted", confidence=0.01)
    await db_session.commit()

    written = await services.score_workspace_candidates(
        db_session, workspace_id=ws, signal_ids=[sig.id], icp=icp
    )
    await db_session.commit()

    rows = list(
        (await db_session.execute(select(WorkspaceScore).where(WorkspaceScore.workspace_id == ws)))
        .scalars()
        .all()
    )
    assert written == 0
    assert rows == []


@pytest.mark.asyncio
@_skip_no_db
async def test_backfill_workspace_isolation(db_session: Any) -> None:
    """Backfill for workspace A does not create score rows for workspace B."""
    from sqlalchemy import select

    from civicsignals_api.modules.signals import services
    from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore

    ws_a = await _make_workspace(db_session, email="iso-a-f6@example.com")
    ws_b = await _make_workspace(db_session, email="iso-b-f6@example.com")
    icp_a = await _make_icp(db_session, workspace_id=ws_a, signal_types=["rfp_posted"])
    await _make_icp(db_session, workspace_id=ws_b, signal_types=["rfp_posted"])
    sig = await _make_signal(db_session, signal_type="rfp_posted")
    await db_session.commit()

    await services.score_workspace_candidates(
        db_session, workspace_id=ws_a, signal_ids=[sig.id], icp=icp_a
    )
    await db_session.commit()

    rows_a = list(
        (
            await db_session.execute(
                select(WorkspaceScore).where(WorkspaceScore.workspace_id == ws_a)
            )
        )
        .scalars()
        .all()
    )
    rows_b = list(
        (
            await db_session.execute(
                select(WorkspaceScore).where(WorkspaceScore.workspace_id == ws_b)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows_a) == 1
    assert rows_b == []


@pytest.mark.asyncio
@_skip_no_db
async def test_icp_changed_event_enqueues_task(db_session: Any) -> None:
    """Publishing icp.changed triggers the listener and enqueues the Celery task."""
    from civicsignals_api import events
    from civicsignals_api.modules.signals import listeners

    ws = await _make_workspace(db_session, email="trigger-sync@example.com")
    await _make_icp(db_session, workspace_id=ws, signal_types=["rfp_posted"])
    await _make_signal(db_session, signal_type="rfp_posted")
    await db_session.commit()

    listeners.register_listeners()
    try:
        with patch("civicsignals_api.modules.signals.tasks.rescore_workspace") as mock_task:
            await events.publish(events.ICP_CHANGED, {"workspace_id": str(ws)})
    finally:
        listeners.unregister_listeners()

    mock_task.delay.assert_called_once_with(str(ws))
