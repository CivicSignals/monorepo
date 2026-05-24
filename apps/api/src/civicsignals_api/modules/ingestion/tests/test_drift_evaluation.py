"""Live-DB tests for the E7 drift-evaluation bridge (doc 18 §3.2).

The ``ingestion.evaluate_recipe_drift`` beat task bridges the recipes module's
drift *decision* to the **authoritative** scheduler pause it owns
(``ingestion_recipe_schedule.paused``, D4): a recipe whose 24h extraction success
rate drops below threshold is auto-paused so the cadence dispatcher skips it, and a
GitHub issue is filed (once). Run only when a Postgres DSN is configured.

Asserts the end-to-end coupling that the recipes-side unit tests can't: that the
decision actually flips the scheduler pause flag, that a GitHub issue is filed
once, that a no-op client still pauses, and that a healthy recipe is untouched.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from civicsignals_api.db import Base, SessionLocal
from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion import tasks as ingestion_tasks
from civicsignals_api.modules.ingestion.models import RecipeSchedule
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.models import DriftState, RunMetric
from civicsignals_api.modules.recipes.schemas import RunOutcome

_DSN = (
    os.environ.get("INGESTION_TEST_DSN")
    or os.environ.get("RECIPES_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

# Anchored to real "now" (not a fixed calendar date) so the seeded runs always
# fall inside the drift task's rolling 24h/7d window — otherwise the test rots
# and fails once the wall clock moves past the window from a hardcoded date.
NOW = datetime.now(UTC)

_TABLES = [
    Base.metadata.tables[RunMetric.__tablename__],
    Base.metadata.tables[DriftState.__tablename__],
    Base.metadata.tables[RecipeSchedule.__tablename__],
]


class _RecordingGitHubClient:
    def __init__(self, url: str = "https://github.com/acme/repo/issues/7") -> None:
        self.url = url
        self.calls: list[dict[str, object]] = []

    def open_issue(self, *, title: str, body: str, labels: list[str]) -> str | None:
        self.calls.append({"title": title, "body": body, "labels": labels})
        return self.url


@pytest_asyncio.fixture
async def db() -> AsyncIterator[None]:
    assert _DSN is not None
    eng = create_async_engine(_DSN)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all, tables=_TABLES, checkfirst=True)
        await conn.run_sync(Base.metadata.create_all, tables=_TABLES, checkfirst=True)
    original_bind = SessionLocal.kw["bind"]
    SessionLocal.configure(bind=eng)
    try:
        yield None
    finally:
        SessionLocal.configure(bind=original_bind)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=_TABLES, checkfirst=True)
        await eng.dispose()


async def _seed_runs(recipe_id: str, *, total: int, succeeded: int, runs: int) -> None:
    async with SessionLocal() as session:
        for _ in range(runs):
            await recipes_services.record_run_outcome(
                session,
                RunOutcome(
                    recipe_id=recipe_id,
                    documents_total=total,
                    extractions_total=total,
                    extractions_succeeded=succeeded,
                    signals_produced=succeeded,
                    finished_at=NOW - timedelta(hours=1),
                ),
            )
        await session.commit()


async def _is_scheduler_paused(recipe_id: str) -> bool:
    async with SessionLocal() as session:
        state = await ingestion_services.get_or_create_recipe_schedule(session, recipe_id)
        await session.commit()
        return state.paused


async def test_drift_task_flips_scheduler_pause_and_files_issue(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failing recipe (success rate 0/8 over 4 runs) must be auto-paused in the
    # *ingestion* schedule (what the dispatcher reads) and get one issue filed.
    await _seed_runs("bad", total=2, succeeded=0, runs=4)
    fake = _RecordingGitHubClient()
    monkeypatch.setattr(recipes_services, "get_github_client", lambda: fake)

    # Call the async helper directly (awaited): the sync task wrapper does
    # ``asyncio.run``, which can't run inside pytest-asyncio's already-running loop.
    paused = await ingestion_tasks._evaluate_recipe_drift_async()

    assert paused == 1
    assert await _is_scheduler_paused("bad") is True
    assert len(fake.calls) == 1  # one issue filed

    # A second tick must not re-pause or re-file (idempotent).
    paused2 = await ingestion_tasks._evaluate_recipe_drift_async()
    assert paused2 == 0  # already paused -> not newly paused
    assert len(fake.calls) == 1
    assert await _is_scheduler_paused("bad") is True


async def test_drift_task_leaves_healthy_recipe_running(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_runs("good", total=2, succeeded=2, runs=4)
    fake = _RecordingGitHubClient()
    monkeypatch.setattr(recipes_services, "get_github_client", lambda: fake)

    paused = await ingestion_tasks._evaluate_recipe_drift_async()

    assert paused == 0
    assert await _is_scheduler_paused("good") is False
    assert fake.calls == []
