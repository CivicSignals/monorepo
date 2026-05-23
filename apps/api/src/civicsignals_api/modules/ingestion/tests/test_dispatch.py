"""DB-backed tests for the cadence dispatcher (D4; doc 18 §3, §6).

``ingestion.dispatch_due_recipes`` scans active recipes, decides which are due
against their ``ingestion_recipe_schedule`` run-state, enqueues
``ingestion.crawl_recipe`` for the due ones, and advances their run-state. These
assertions touch the ``ingestion_recipe_schedule`` table (Boolean + tz datetimes)
so they need a real Postgres and skip cleanly without a DSN — same opt-in-via-env
convention as ``test_raw_document`` (CI sets ``DATABASE_DIRECT_URL``).

The async dispatcher *body* (``_dispatch_due_recipes_async``) is awaited directly
in the due/state-update tests (the public ``dispatch_due_recipes`` wraps it in
``asyncio.run`` after the leader gate, which can't nest inside pytest-asyncio's
loop). The leader gate itself is covered by the synchronous tests at the bottom
and by ``test_locks``. The recipe set, the wall clock, the Redis client, and
``crawl_recipe.delay`` are stubbed so the test drives the *decision* logic against
a real schedule table without a broker or on-disk recipes.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import create_async_engine

from civicsignals_api.db import Base, SessionLocal
from civicsignals_api.modules.ingestion import locks as locks_module
from civicsignals_api.modules.ingestion import scheduler as scheduler_module
from civicsignals_api.modules.ingestion import services, tasks
from civicsignals_api.modules.ingestion.models import RecipeSchedule  # noqa: F401 (register table)
from civicsignals_api.modules.ingestion.tests.test_scheduler import _recipe
from civicsignals_api.modules.recipes.schemas import Recipe

_DSN = (
    os.environ.get("INGESTION_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

_TABLE = "ingestion_recipe_schedule"


def _aware(value: datetime | None) -> datetime:
    """Normalize a DB-returned datetime to tz-aware UTC for comparison."""
    assert value is not None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@pytest_asyncio.fixture
async def db() -> AsyncIterator[None]:
    """Create the schedule table on the real DB and point SessionLocal at it."""
    assert _DSN is not None
    table: Table = Base.metadata.tables[_TABLE]
    test_engine = create_async_engine(_DSN)
    async with test_engine.begin() as conn:
        await conn.run_sync(table.drop, checkfirst=True)
        await conn.run_sync(table.create, checkfirst=True)
    # The dispatcher opens its own session via SessionLocal; rebind it to the
    # test engine so the dispatcher and the assertions share one database. Restore
    # the original engine on teardown so other tests are unaffected.
    original_bind = SessionLocal.kw["bind"]
    SessionLocal.configure(bind=test_engine)
    try:
        yield None
    finally:
        SessionLocal.configure(bind=original_bind)
        async with test_engine.begin() as conn:
            await conn.run_sync(table.drop, checkfirst=True)
        await test_engine.dispose()


def _patch_scan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    recipes: list[Recipe],
    now: datetime,
    enqueued: list[tuple[str, int]],
) -> None:
    """Stub the recipe set, the clock, and ``crawl_recipe.delay`` for a tick."""
    monkeypatch.setattr(scheduler_module, "load_active_recipes", lambda: recipes)
    monkeypatch.setattr(scheduler_module, "utcnow", lambda: now)
    monkeypatch.setattr(tasks.crawl_recipe, "delay", lambda *args: enqueued.append(args))


async def test_dispatch_enqueues_only_due_recipes(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    # due: every-2h recipe whose stored next_run_at is in the past.
    # not_due: every-2h recipe whose stored next_run_at is in the future.
    due = _recipe("due-recipe", cron="0 */2 * * *", jitter_seconds=0)
    not_due = _recipe("not-due-recipe", cron="0 */2 * * *", jitter_seconds=0)

    # Seed run-state: due-recipe's next_run is past; not-due's is future.
    async with SessionLocal() as session:
        await services.mark_recipe_dispatched(
            session,
            "due-recipe",
            last_run_at=now - timedelta(hours=2),
            next_run_at=now - timedelta(minutes=1),
            recipe_version=1,
            cron="0 */2 * * *",
        )
        await services.mark_recipe_dispatched(
            session,
            "not-due-recipe",
            last_run_at=now - timedelta(minutes=30),
            next_run_at=now + timedelta(hours=1),
            recipe_version=1,
            cron="0 */2 * * *",
        )
        await session.commit()

    enqueued: list[tuple[str, int]] = []
    _patch_scan(monkeypatch, recipes=[due, not_due], now=now, enqueued=enqueued)

    count = await tasks._dispatch_due_recipes_async()

    assert count == 1
    assert enqueued == [("due-recipe", 1)]

    # Run-state advanced for the dispatched recipe; untouched for the other.
    async with SessionLocal() as session:
        states = {s.recipe_id: s for s in await services.list_recipe_schedules(session)}
    assert _aware(states["due-recipe"].last_run_at) == now
    # next_run is the next 2h boundary after now (12:00) + the recipe's jitter.
    offset = scheduler_module.jitter_offset("due-recipe", scheduler_module.DEFAULT_JITTER_WINDOW)
    assert _aware(states["due-recipe"].next_run_at) == datetime(
        2026, 5, 22, 12, 0, tzinfo=UTC
    ) + timedelta(seconds=offset)
    # not-due was not re-scheduled.
    assert _aware(states["not-due-recipe"].next_run_at) == now + timedelta(hours=1)


async def test_dispatch_bootstraps_never_run_recipe(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 5, 22, 9, 7, 0, tzinfo=UTC)
    fresh = _recipe("fresh-recipe", cron="*/5 * * * *", jitter_seconds=0)

    enqueued: list[tuple[str, int]] = []
    _patch_scan(monkeypatch, recipes=[fresh], now=now, enqueued=enqueued)

    count = await tasks._dispatch_due_recipes_async()

    # A never-seen recipe is created + dispatched on the first tick.
    assert count == 1
    assert enqueued == [("fresh-recipe", 1)]
    async with SessionLocal() as session:
        states = {s.recipe_id: s for s in await services.list_recipe_schedules(session)}
    assert _aware(states["fresh-recipe"].last_run_at) == now
    # next_run advanced to the next */5 boundary after now (09:10) + jitter.
    offset = scheduler_module.jitter_offset("fresh-recipe", scheduler_module.DEFAULT_JITTER_WINDOW)
    assert _aware(states["fresh-recipe"].next_run_at) == datetime(
        2026, 5, 22, 9, 10, tzinfo=UTC
    ) + timedelta(seconds=offset)


async def test_dispatch_skips_paused_recipe(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    paused = _recipe("paused-recipe", cron="*/5 * * * *", jitter_seconds=0)

    async with SessionLocal() as session:
        await services.set_recipe_paused(session, "paused-recipe", paused=True)
        await session.commit()

    enqueued: list[tuple[str, int]] = []
    _patch_scan(monkeypatch, recipes=[paused], now=now, enqueued=enqueued)

    count = await tasks._dispatch_due_recipes_async()
    assert count == 0
    assert enqueued == []


async def test_dispatch_paused_when_backpressure(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    due = _recipe("due-recipe", cron="*/5 * * * *", jitter_seconds=0)

    enqueued: list[tuple[str, int]] = []
    _patch_scan(monkeypatch, recipes=[due], now=now, enqueued=enqueued)
    # Simulate the D15 backpressure hook reporting the ingest queue is over its
    # hard threshold -> the dispatcher must enqueue nothing this tick.
    monkeypatch.setattr(tasks, "_ingest_queue_has_headroom", lambda: False)

    count = await tasks._dispatch_due_recipes_async()
    assert count == 0
    assert enqueued == []
    # No run-state row was created because the scan was skipped entirely.
    async with SessionLocal() as session:
        assert await services.list_recipe_schedules(session) == []


# ----------------------------------------------------------------------------
# Leader gate (synchronous — exercises the public task's leader election). These
# don't touch the DB: a non-leader tick returns before any DB work.
# ----------------------------------------------------------------------------


class _NonLeaderClient:
    """Stub Redis whose NX set always fails -> this process is never leader."""

    def set(self, *a: object, **k: object) -> None:
        return None

    def eval(self, *a: object) -> int:
        return 0


def test_dispatch_no_op_when_not_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-leader scheduler tick enqueues nothing and never touches the DB."""
    enqueued: list[tuple[str, int]] = []
    monkeypatch.setattr(locks_module, "get_redis_client", lambda: _NonLeaderClient())
    monkeypatch.setattr(tasks.crawl_recipe, "delay", lambda *args: enqueued.append(args))
    # If the body were reached it would call load_active_recipes; make that blow up
    # so the test fails loudly if the leader gate doesn't short-circuit.
    monkeypatch.setattr(
        scheduler_module,
        "load_active_recipes",
        lambda: (_ for _ in ()).throw(AssertionError("body ran despite not-leader")),
    )

    assert tasks.dispatch_due_recipes() == 0
    assert enqueued == []


# ----------------------------------------------------------------------------
# Per-recipe run lock on the crawl task (synchronous; no DB). A held run lock
# makes a duplicate / overlapping crawl a no-op rather than a concurrent run.
# ----------------------------------------------------------------------------


class _HeldRunLockClient(_NonLeaderClient):
    """NX set fails -> the run lock is already held by an in-flight crawl."""


class _FreeRunLockClient:
    """NX set succeeds -> the run lock was free and is now acquired."""

    def set(self, *a: object, **k: object) -> bool:
        return True

    def eval(self, *a: object) -> int:
        return 1


def _record_crawl(ran: list[str], records: list[str]) -> object:
    """A stub for the raw-doc-collecting crawl seam that records the call.

    Returns ``(records, [])`` — no raw docs — so the task's persistence branch
    (``store_crawled_raw_documents``) is skipped, keeping this a broker-free unit
    test of the run-lock + record-count behaviour (D4 raw-doc storage is exercised
    end-to-end in ``extraction/tests/test_pipeline_chain_e2e.py``).
    """

    def _crawl(recipe_id: str, seed_urls: object) -> tuple[list[str], list[object]]:
        ran.append(recipe_id)
        return records, []

    return _crawl


def test_crawl_recipe_skips_when_run_lock_held(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr(locks_module, "get_redis_client", lambda: _HeldRunLockClient())
    monkeypatch.setattr(
        "civicsignals_api.modules.ingestion.services."
        "crawl_recipe_with_connector_collecting_raw_documents",
        _record_crawl(ran, []),
    )
    # Lock held -> the crawl body must not run; returns 0.
    assert tasks.crawl_recipe("wa-state-webs", 1) == 0
    assert ran == []


def test_crawl_recipe_runs_when_run_lock_free(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []
    monkeypatch.setattr(locks_module, "get_redis_client", lambda: _FreeRunLockClient())
    monkeypatch.setattr(
        "civicsignals_api.modules.ingestion.services."
        "crawl_recipe_with_connector_collecting_raw_documents",
        _record_crawl(ran, ["rec-1", "rec-2"]),
    )
    # Lock free -> the crawl runs and returns the record count.
    assert tasks.crawl_recipe("wa-state-webs", 1) == 2
    assert ran == ["wa-state-webs"]
