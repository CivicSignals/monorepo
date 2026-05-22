"""Live-DB tests for E7 recipe drift detection (doc 18 §3.2).

Run against a real Postgres only when ``RECIPES_TEST_DSN`` (or ``DATABASE_DIRECT_URL``)
is set (CI / a developer with the dev stack up); otherwise they skip — the
``recipes_run_metric`` / ``recipes_drift_state`` tables use Postgres UUID +
tz-aware timestamps, so a faithful substitute needs real Postgres (mirrors the
extraction / ingestion DB-gated suites).

Covers the recipes-side service path end to end (the *decision* + bookkeeping;
the authoritative scheduler pause it drives lives in ingestion and is exercised in
``ingestion/tests/test_drift_evaluation.py``):
- ``record_run_outcome`` -> ``compute_rolling_metrics`` rolling-window aggregation;
- success-rate below threshold -> ``should_pause`` true + drift bookkeeping recorded;
- the LLM-fallback-rate metric/alert over the 7d window;
- ``evaluate_recipe_drift`` opens exactly one GitHub issue (idempotent) via a
  recording fake, is a no-op with the no-op client (unconfigured), and is
  best-effort when the client raises;
- ``clear_drift_state`` resets the bookkeeping so the next breakage files anew.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from civicsignals_api.config import Settings
from civicsignals_api.db import Base
from civicsignals_api.modules.recipes import drift
from civicsignals_api.modules.recipes.github_client import NoopGitHubClient
from civicsignals_api.modules.recipes.models import DriftState, RunMetric
from civicsignals_api.modules.recipes.schemas import RunOutcome

_DSN = os.environ.get("RECIPES_TEST_DSN") or os.environ.get("DATABASE_DIRECT_URL")
pytestmark = pytest.mark.skipif(
    not _DSN,
    reason="neither RECIPES_TEST_DSN nor DATABASE_DIRECT_URL set; needs a live Postgres",
)

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


class _RecordingGitHubClient:
    def __init__(self, url: str = "https://github.com/acme/repo/issues/42") -> None:
        self.url = url
        self.calls: list[dict[str, object]] = []

    def open_issue(self, *, title: str, body: str, labels: list[str]) -> str | None:
        self.calls.append({"title": title, "body": body, "labels": labels})
        return self.url


class _RaisingGitHubClient:
    def open_issue(self, *, title: str, body: str, labels: list[str]) -> str | None:
        raise RuntimeError("github 503")


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    assert _DSN is not None
    eng = create_async_engine(_DSN)
    owned = [
        Base.metadata.tables[RunMetric.__tablename__],
        Base.metadata.tables[DriftState.__tablename__],
    ]
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=owned, checkfirst=True)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=owned, checkfirst=True)
        await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s


def _settings() -> Settings:
    return Settings(
        github_token=None,
        drift_extraction_success_threshold=0.5,
        drift_llm_fallback_threshold=0.2,
        drift_min_runs_for_pause=3,
    )


def _outcome(
    recipe_id: str,
    *,
    age_hours: float,
    total: int,
    succeeded: int,
    signals: int = 0,
    llm: int = 0,
    fields: int = 0,
) -> RunOutcome:
    return RunOutcome(
        recipe_id=recipe_id,
        documents_total=total,
        extractions_total=total,
        extractions_succeeded=succeeded,
        signals_produced=signals,
        llm_fallbacks=llm,
        fields_total=fields,
        finished_at=NOW - timedelta(hours=age_hours),
    )


async def test_record_and_compute_rolling_metrics(session: AsyncSession) -> None:
    await drift.record_run_outcome(
        session, _outcome("r1", age_hours=1, total=4, succeeded=3, signals=3)
    )
    await drift.record_run_outcome(
        session, _outcome("r1", age_hours=23, total=2, succeeded=1, signals=1)
    )
    # Outside the 24h window — excluded from the 24h aggregate.
    await drift.record_run_outcome(session, _outcome("r1", age_hours=30, total=10, succeeded=0))
    await session.commit()

    m24 = await drift.compute_rolling_metrics(
        session, "r1", window_hours=drift.WINDOW_24H_HOURS, now=NOW
    )
    assert m24.runs == 2
    assert m24.extractions_total == 6
    assert m24.extractions_succeeded == 4
    assert m24.extraction_success_rate == pytest.approx(4 / 6)

    m7 = await drift.compute_rolling_metrics(
        session, "r1", window_hours=drift.WINDOW_7D_HOURS, now=NOW
    )
    assert m7.runs == 3  # the 30h-old run is inside 7d


async def test_threshold_decides_pause_and_records_bookkeeping(session: AsyncSession) -> None:
    # 5 runs, mostly failing -> success rate 1/10 = 0.1 < 0.5, >= min_runs.
    for _ in range(4):
        await drift.record_run_outcome(session, _outcome("bad", age_hours=1, total=2, succeeded=0))
    await drift.record_run_outcome(session, _outcome("bad", age_hours=1, total=2, succeeded=1))
    await session.commit()

    fake = _RecordingGitHubClient()
    result = await drift.evaluate_recipe_drift(
        session, "bad", fake, already_paused=False, settings=_settings(), now=NOW
    )
    await session.commit()

    assert result.should_pause is True
    assert result.newly_paused is True  # was not already paused
    # The recipes-side drift bookkeeping recorded the pause decision.
    state = await drift.get_drift_state(session, "bad")
    assert state is not None
    assert state.drift_paused is True
    assert state.paused_reason is not None


async def test_already_paused_recipe_is_not_newly_paused(session: AsyncSession) -> None:
    for _ in range(4):
        await drift.record_run_outcome(session, _outcome("bad2", age_hours=1, total=2, succeeded=0))
    await session.commit()
    fake = _RecordingGitHubClient()
    result = await drift.evaluate_recipe_drift(
        session, "bad2", fake, already_paused=True, settings=_settings(), now=NOW
    )
    await session.commit()
    assert result.should_pause is True
    assert result.newly_paused is False  # caller said it was already paused


async def test_does_not_pause_healthy_recipe(session: AsyncSession) -> None:
    for _ in range(4):
        await drift.record_run_outcome(
            session, _outcome("good", age_hours=1, total=2, succeeded=2, signals=2)
        )
    await session.commit()
    fake = _RecordingGitHubClient()
    result = await drift.evaluate_recipe_drift(session, "good", fake, settings=_settings(), now=NOW)
    await session.commit()
    assert result.should_pause is False
    assert result.issue_url is None
    assert fake.calls == []


async def test_llm_fallback_rate_metric_and_alert(session: AsyncSession) -> None:
    # Healthy extraction success but heavy LLM fallback over 7d (degradation alert).
    await drift.record_run_outcome(
        session,
        _outcome("degrading", age_hours=2, total=5, succeeded=5, signals=5, llm=6, fields=20),
    )
    await session.commit()
    fake = _RecordingGitHubClient()
    result = await drift.evaluate_recipe_drift(
        session, "degrading", fake, settings=_settings(), now=NOW
    )
    assert result.window_7d.llm_fallback_rate == pytest.approx(0.3)
    assert result.llm_fallback_alert is True
    # High LLM fallback alone does not auto-pause (only success-rate does).
    assert result.should_pause is False


async def test_auto_issue_opens_once_idempotent(session: AsyncSession) -> None:
    for _ in range(4):
        await drift.record_run_outcome(
            session, _outcome("flaky", age_hours=1, total=2, succeeded=0)
        )
    await session.commit()

    fake = _RecordingGitHubClient()
    first = await drift.evaluate_recipe_drift(
        session, "flaky", fake, settings=_settings(), now=NOW, failing_sample="oops"
    )
    await session.commit()
    assert first.issue_url == fake.url
    assert len(fake.calls) == 1

    # Re-evaluating while still drifting must NOT file a second issue.
    second = await drift.evaluate_recipe_drift(
        session, "flaky", fake, already_paused=True, settings=_settings(), now=NOW
    )
    await session.commit()
    assert second.should_pause is True
    assert second.issue_url == fake.url
    assert len(fake.calls) == 1  # idempotent


async def test_no_issue_when_unconfigured_but_still_decides_pause(session: AsyncSession) -> None:
    for _ in range(4):
        await drift.record_run_outcome(
            session, _outcome("noconfig", age_hours=1, total=2, succeeded=0)
        )
    await session.commit()

    noop = NoopGitHubClient()
    result = await drift.evaluate_recipe_drift(
        session, "noconfig", noop, settings=_settings(), now=NOW
    )
    await session.commit()
    # The pause decision still stands; no issue URL recorded (no-op client).
    assert result.should_pause is True
    assert result.issue_url is None
    state = await drift.get_drift_state(session, "noconfig")
    assert state is not None and state.drift_issue_url is None  # next tick can still file


async def test_issue_open_failure_still_decides_pause_and_retries(session: AsyncSession) -> None:
    for _ in range(4):
        await drift.record_run_outcome(
            session, _outcome("ghdown", age_hours=1, total=2, succeeded=0)
        )
    await session.commit()
    # The GitHub client raises, but the pause decision must still stand (filing the
    # issue is a notification, not the pause). No idempotency key recorded -> retry.
    result = await drift.evaluate_recipe_drift(
        session, "ghdown", _RaisingGitHubClient(), settings=_settings(), now=NOW
    )
    await session.commit()
    assert result.should_pause is True
    assert result.issue_url is None
    state = await drift.get_drift_state(session, "ghdown")
    assert state is not None and state.drift_issue_url is None


async def test_clear_drift_state_resets_bookkeeping(session: AsyncSession) -> None:
    for _ in range(4):
        await drift.record_run_outcome(
            session, _outcome("fixme", age_hours=1, total=2, succeeded=0)
        )
    await session.commit()
    fake = _RecordingGitHubClient()
    await drift.evaluate_recipe_drift(session, "fixme", fake, settings=_settings(), now=NOW)
    await session.commit()
    state = await drift.get_drift_state(session, "fixme")
    assert state is not None and state.drift_paused is True and state.drift_issue_url is not None

    await drift.clear_drift_state(session, "fixme")
    await session.commit()
    cleared = await drift.get_drift_state(session, "fixme")
    assert cleared is not None
    assert cleared.drift_paused is False
    assert cleared.drift_issue_url is None  # next breakage files anew


async def test_record_drift_pause_is_idempotent(session: AsyncSession) -> None:
    await drift.record_drift_pause(session, "m", reason="first", now=NOW)
    await session.commit()
    first = await drift.get_drift_state(session, "m")
    assert first is not None and first.paused_at == NOW

    # Re-recording preserves the original paused_at (idempotent), updates the reason.
    await drift.record_drift_pause(session, "m", reason="again", now=NOW + timedelta(hours=1))
    await session.commit()
    second = await drift.get_drift_state(session, "m")
    assert second is not None
    assert second.paused_at == NOW  # unchanged
    assert second.paused_reason == "again"
