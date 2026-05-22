"""Tests for QA-7: extraction quality sampling process.

Coverage:

**Pure unit tests (no DB):**
- :func:`_week_label` ISO-week formatting.
- :func:`_compute_overall_accuracy` fraction maths (all correct, mixed, all
  unknown, empty).

**Live-DB tests** (gated on RECIPES_TEST_DSN / DATABASE_DIRECT_URL):
- Sampler creates ~N sample rows from seeded signals.
- Idempotent per ISO-week window (second call for the same week returns existing
  rows, no duplicates).
- Respects the ``n`` limit — never exceeds the requested sample size.
- :func:`record_field_accuracy` updates a sample: field_judgements + overall
  accuracy + reviewer + reviewed_at populated.
- Recording fields marks the sample reviewed (no longer pending).
- Invalid verdict raises :exc:`InvalidVerdict`.
- Missing sample raises :exc:`SampleNotFound`.
- Per-recipe rollup aggregates correctly across multiple recipes:
  - one accurate recipe (all correct) → overall_accuracy ≈ 1.0
  - one poor recipe (all incorrect) → overall_accuracy ≈ 0.0
- ``window`` filter scopes the rollup to one ISO week.
- Empty / no-samples handled (no error, empty results).

**HTTP endpoint tests (TestClient, no live DB):**
- ``GET /api/v1/recipes/quality-samples`` — 401 without auth, 403 without
  admin role (mocked), 200 with admin (mocked empty DB).
- ``POST /api/v1/recipes/quality-samples/{id}/judgements`` — 401 without auth.
- ``GET /api/v1/recipes/quality-accuracy`` — 401 without auth.
- ``GET /api/v1/recipes/quality-accuracy/{recipe_id}`` — 401 without auth.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from civicsignals_api.db import Base
from civicsignals_api.modules.recipes.models import QualitySample
from civicsignals_api.modules.recipes.quality_sampling import (
    VERDICT_CORRECT,
    VERDICT_INCORRECT,
    VERDICT_UNKNOWN,
    InvalidVerdict,
    SampleNotFound,
    _compute_overall_accuracy,
    _week_label,
    get_recipe_accuracy,
    list_pending_samples,
    list_recipe_accuracy,
    record_field_accuracy,
    sample_extraction_quality,
)

_DSN = os.environ.get("RECIPES_TEST_DSN") or os.environ.get("DATABASE_DIRECT_URL")

# Fixed "now" for deterministic tests.
NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
# 2026-W21 (week 21 of 2026, starting Monday 2026-05-18)
WEEK_LABEL = "2026-W21"


# ---------------------------------------------------------------------------
# Pure unit tests (no DB)
# ---------------------------------------------------------------------------


class TestWeekLabel:
    def test_thursday(self) -> None:
        # 2026-05-22 is a Thursday; ISO week 21.
        assert _week_label(NOW) == "2026-W21"

    def test_monday(self) -> None:
        monday = datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC)
        assert _week_label(monday) == "2026-W21"

    def test_sunday(self) -> None:
        # Sunday 2026-05-24 is still in W21.
        sunday = datetime(2026, 5, 24, 23, 59, 59, tzinfo=UTC)
        assert _week_label(sunday) == "2026-W21"

    def test_next_monday_new_week(self) -> None:
        # Monday 2026-05-25 starts W22.
        next_monday = datetime(2026, 5, 25, 0, 0, 0, tzinfo=UTC)
        assert _week_label(next_monday) == "2026-W22"

    def test_year_boundary(self) -> None:
        # 2026-01-01 is Thursday of W01 (ISO week 1 of 2026).
        jan1 = datetime(2026, 1, 1, tzinfo=UTC)
        assert _week_label(jan1) == "2026-W01"


class TestComputeOverallAccuracy:
    def test_all_correct(self) -> None:
        j = {"a": VERDICT_CORRECT, "b": VERDICT_CORRECT}
        assert _compute_overall_accuracy(j) == pytest.approx(1.0)

    def test_all_incorrect(self) -> None:
        j = {"a": VERDICT_INCORRECT, "b": VERDICT_INCORRECT}
        assert _compute_overall_accuracy(j) == pytest.approx(0.0)

    def test_mixed(self) -> None:
        j = {"a": VERDICT_CORRECT, "b": VERDICT_INCORRECT, "c": VERDICT_CORRECT}
        assert _compute_overall_accuracy(j) == pytest.approx(2 / 3)

    def test_all_unknown_returns_none(self) -> None:
        j = {"a": VERDICT_UNKNOWN, "b": VERDICT_UNKNOWN}
        assert _compute_overall_accuracy(j) is None

    def test_unknown_excluded(self) -> None:
        # unknown fields don't count in numerator or denominator.
        j = {"a": VERDICT_CORRECT, "b": VERDICT_UNKNOWN}
        assert _compute_overall_accuracy(j) == pytest.approx(1.0)

    def test_empty_returns_none(self) -> None:
        assert _compute_overall_accuracy({}) is None


# ---------------------------------------------------------------------------
# Live-DB helpers and fixtures
# ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not _DSN,
    reason="neither RECIPES_TEST_DSN nor DATABASE_DIRECT_URL set; needs a live Postgres",
)


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Create the tables needed for QA-7 tests and tear them down afterward."""
    if not _DSN:
        pytest.skip("neither RECIPES_TEST_DSN nor DATABASE_DIRECT_URL set")
    eng = create_async_engine(_DSN)

    # Tables owned by the recipes module that the sampler touches.
    # Also need signals_signal for the random-sample query (read-only).
    from civicsignals_api.modules.signals.models import Signal

    quality_table = Base.metadata.tables[QualitySample.__tablename__]
    signal_table = Base.metadata.tables[Signal.__tablename__]

    async with eng.begin() as conn:
        # Create signals_signal first (the sampler SELECTs from it).
        await conn.run_sync(Base.metadata.create_all, tables=[signal_table], checkfirst=True)
        await conn.run_sync(Base.metadata.create_all, tables=[quality_table], checkfirst=True)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=[quality_table], checkfirst=True)
        await eng.dispose()


@pytest_asyncio.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    from sqlalchemy import text

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        # Each test starts from a clean slate: remove any quality samples and the
        # QA-7-seeded signals left behind by a previous test in this session.
        # signals_signal persists across tests (the engine fixture only manages
        # recipes_quality_sample), so leftover QA-7 signals would otherwise pollute
        # window-based assertions (e.g. the no-candidates test).
        await s.execute(text("DELETE FROM recipes_quality_sample"))
        await s.execute(text("DELETE FROM signals_signal WHERE recipe_id LIKE 'qa7-%'"))
        await s.commit()
        try:
            yield s
        finally:
            await s.rollback()
            await s.execute(text("DELETE FROM recipes_quality_sample"))
            await s.execute(text("DELETE FROM signals_signal WHERE recipe_id LIKE 'qa7-%'"))
            await s.commit()


def _make_signal(
    recipe_id: str, *, age_days: float = 1.0, now: datetime = NOW
) -> dict[str, object]:
    """Return kwargs to insert a minimal signal row into ``signals_signal``."""
    from civicsignals_api.ids import uuid7

    return {
        "id": uuid7(),
        "recipe_id": recipe_id,
        "signal_type": "rfp_posted",
        "content_hash": uuid.uuid4().hex,
        "title": f"Test signal {recipe_id}",
        "summary": "Test summary",
        # asyncpg requires JSONB values to be pre-serialized to a JSON string
        # when passed as raw text() SQL parameters.
        "details": json.dumps({"title": "Test signal", "amount": "50000"}),
        "status": "new",
        "is_degraded": False,
        "review_required": False,
        "observed_at": now - timedelta(days=age_days),
    }


async def _insert_signals(session: AsyncSession, *rows: dict[str, object]) -> None:
    """Raw-insert signal rows into signals_signal without the model import."""
    from sqlalchemy import text

    for row in rows:
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        await session.execute(
            text(f"INSERT INTO signals_signal ({cols}) VALUES ({placeholders})"),
            row,
        )
    await session.flush()


# ---------------------------------------------------------------------------
# Live-DB: sampler behaviour
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_sampler_creates_sample_rows(db_session: AsyncSession) -> None:
    """Sampler inserts one row per selected signal."""
    recipe_id = "qa7-test-recipe-a"
    signals = [_make_signal(recipe_id, age_days=i * 0.1) for i in range(5)]
    await _insert_signals(db_session, *signals)

    samples = await sample_extraction_quality(db_session, now=NOW, n=3)
    await db_session.commit()

    assert len(samples) <= 3
    assert len(samples) > 0
    assert all(s.sample_window == WEEK_LABEL for s in samples)
    assert all(s.reviewed_at is None for s in samples)


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_sampler_idempotent_per_week(db_session: AsyncSession) -> None:
    """A second call in the same week returns existing rows, inserts nothing."""
    recipe_id = "qa7-test-recipe-b"
    signals = [_make_signal(recipe_id, age_days=0.5)]
    await _insert_signals(db_session, *signals)

    first = await sample_extraction_quality(db_session, now=NOW, n=10)
    await db_session.commit()

    # Call again with the same week.
    second = await sample_extraction_quality(db_session, now=NOW, n=10)
    await db_session.commit()

    assert len(second) == len(first)
    first_ids = {s.id for s in first}
    second_ids = {s.id for s in second}
    assert first_ids == second_ids


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_sampler_respects_n_limit(db_session: AsyncSession) -> None:
    """Sampler never creates more than ``n`` rows."""
    recipe_id = "qa7-test-recipe-c"
    signals = [_make_signal(recipe_id, age_days=i * 0.05) for i in range(20)]
    await _insert_signals(db_session, *signals)

    samples = await sample_extraction_quality(db_session, now=NOW, n=5)
    await db_session.commit()

    assert len(samples) <= 5


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_sampler_empty_when_no_candidates(db_session: AsyncSession) -> None:
    """Returns empty list when no signals are within the signal window.

    Anchored at a far-future ``now`` with a 1-day window so that *no* signal in a
    shared test DB — QA-7-seeded or otherwise — can fall inside the candidate
    window. (The db_session fixture also removes QA-7 signals between tests.)
    """
    far_future = datetime(2099, 1, 1, 12, 0, 0, tzinfo=UTC)
    samples = await sample_extraction_quality(
        db_session, now=far_future, n=10, signal_window_days=1
    )
    await db_session.commit()
    assert samples == []


# ---------------------------------------------------------------------------
# Live-DB: recording per-field accuracy
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_record_field_accuracy_updates_sample(db_session: AsyncSession) -> None:
    """Recording judgements updates the sample row."""
    recipe_id = "qa7-test-recipe-d"
    await _insert_signals(db_session, _make_signal(recipe_id, age_days=1))
    samples = await sample_extraction_quality(db_session, now=NOW, n=1)
    await db_session.commit()
    assert samples

    sample = samples[0]
    judgements = {"title": VERDICT_CORRECT, "amount": VERDICT_INCORRECT}
    updated = await record_field_accuracy(
        db_session,
        sample.id,
        field_judgements=judgements,
        reviewer="reviewer@example.com",
        now=NOW,
    )
    await db_session.commit()

    assert updated.field_judgements == judgements
    assert updated.overall_accuracy == pytest.approx(0.5)  # 1 correct / 2 judged
    assert updated.reviewer == "reviewer@example.com"
    assert updated.reviewed_at is not None


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_record_field_accuracy_marks_not_pending(db_session: AsyncSession) -> None:
    """After recording, the sample should no longer appear in list_pending_samples."""
    recipe_id = "qa7-test-recipe-e"
    await _insert_signals(db_session, _make_signal(recipe_id, age_days=1))
    samples = await sample_extraction_quality(db_session, now=NOW, n=1)
    await db_session.commit()
    assert samples

    sample = samples[0]
    pending_before = await list_pending_samples(db_session)
    assert any(s.id == sample.id for s in pending_before)

    await record_field_accuracy(
        db_session,
        sample.id,
        field_judgements={"title": VERDICT_CORRECT},
        reviewer="qa@example.com",
    )
    await db_session.commit()

    pending_after = await list_pending_samples(db_session)
    assert not any(s.id == sample.id for s in pending_after)


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_record_field_accuracy_invalid_verdict(db_session: AsyncSession) -> None:
    """Invalid verdict string raises InvalidVerdict before DB write."""
    recipe_id = "qa7-test-recipe-f"
    await _insert_signals(db_session, _make_signal(recipe_id, age_days=1))
    samples = await sample_extraction_quality(db_session, now=NOW, n=1)
    await db_session.commit()
    assert samples

    with pytest.raises(InvalidVerdict):
        await record_field_accuracy(
            db_session,
            samples[0].id,
            field_judgements={"title": "maybe"},
            reviewer="qa@example.com",
        )


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_record_field_accuracy_missing_sample(db_session: AsyncSession) -> None:
    """Recording to a non-existent sample raises SampleNotFound."""
    with pytest.raises(SampleNotFound):
        await record_field_accuracy(
            db_session,
            uuid.uuid4(),
            field_judgements={"title": VERDICT_CORRECT},
            reviewer="qa@example.com",
        )


# ---------------------------------------------------------------------------
# Live-DB: per-recipe accuracy rollup
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_recipe_accuracy_rollup(db_session: AsyncSession) -> None:
    """Per-recipe rollup: accurate recipe ≈ 1.0, poor recipe ≈ 0.0."""
    good_recipe = "qa7-test-recipe-good"
    bad_recipe = "qa7-test-recipe-bad"

    # Insert two signals for the good recipe and one for the bad.
    await _insert_signals(
        db_session,
        _make_signal(good_recipe, age_days=1),
        _make_signal(good_recipe, age_days=2),
        _make_signal(bad_recipe, age_days=1),
    )

    # Use different week labels so good/bad don't share idempotency keys.
    good_now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
    bad_now = datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)  # W22

    good_samples = await sample_extraction_quality(db_session, now=good_now, n=10)
    await db_session.commit()
    bad_samples = await sample_extraction_quality(db_session, now=bad_now, n=10)
    await db_session.commit()

    # Find a sample for each recipe.
    good_sample = next(s for s in good_samples if s.recipe_id == good_recipe)
    bad_sample = next((s for s in bad_samples if s.recipe_id == bad_recipe), None)
    if bad_sample is None:
        # bad_recipe may have landed in good_samples if signal_window_days covers both.
        bad_sample = next(s for s in good_samples if s.recipe_id == bad_recipe)

    # Record all-correct for the good recipe.
    await record_field_accuracy(
        db_session,
        good_sample.id,
        field_judgements={"title": VERDICT_CORRECT, "amount": VERDICT_CORRECT},
        reviewer="qa@example.com",
    )
    # Record all-incorrect for the bad recipe.
    await record_field_accuracy(
        db_session,
        bad_sample.id,
        field_judgements={"title": VERDICT_INCORRECT, "amount": VERDICT_INCORRECT},
        reviewer="qa@example.com",
    )
    await db_session.commit()

    good_acc = await get_recipe_accuracy(db_session, good_recipe)
    bad_acc = await get_recipe_accuracy(db_session, bad_recipe)

    assert good_acc.overall_accuracy is not None
    assert good_acc.overall_accuracy == pytest.approx(1.0)
    assert bad_acc.overall_accuracy is not None
    assert bad_acc.overall_accuracy == pytest.approx(0.0)

    # Field-level accuracy.
    assert good_acc.field_accuracy.get("title") == pytest.approx(1.0)
    assert bad_acc.field_accuracy.get("title") == pytest.approx(0.0)


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_recipe_accuracy_empty_no_error(db_session: AsyncSession) -> None:
    """get_recipe_accuracy returns a zero-count rollup for a recipe with no samples."""
    acc = await get_recipe_accuracy(db_session, "qa7-no-samples-recipe")
    assert acc.sample_count == 0
    assert acc.reviewed_count == 0
    assert acc.overall_accuracy is None
    assert acc.field_accuracy == {}


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_list_recipe_accuracy_empty_no_error(db_session: AsyncSession) -> None:
    """list_recipe_accuracy returns empty list when no samples exist."""
    result = await list_recipe_accuracy(db_session)
    assert result == []


@pytest.mark.skipif(not _DSN, reason="needs live Postgres")
async def test_recipe_accuracy_window_filter(db_session: AsyncSession) -> None:
    """window filter scopes the rollup to a specific ISO week."""
    recipe_id = "qa7-test-recipe-window"
    await _insert_signals(db_session, _make_signal(recipe_id, age_days=1))

    # Sample in W21.
    w21_now = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)
    w21_samples = await sample_extraction_quality(db_session, now=w21_now, n=10)
    await db_session.commit()

    if w21_samples:
        sample = next((s for s in w21_samples if s.recipe_id == recipe_id), None)
        if sample:
            await record_field_accuracy(
                db_session,
                sample.id,
                field_judgements={"title": VERDICT_CORRECT},
                reviewer="qa@example.com",
            )
            await db_session.commit()

    # Rollup scoped to W21 should see the reviewed sample.
    _acc_w21 = await get_recipe_accuracy(db_session, recipe_id, window="2026-W21")
    # Rollup scoped to W22 should see nothing (no samples in W22).
    acc_w22 = await get_recipe_accuracy(db_session, recipe_id, window="2026-W22")

    assert acc_w22.sample_count == 0
    assert acc_w22.overall_accuracy is None


# ---------------------------------------------------------------------------
# HTTP endpoint tests (TestClient, no live DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient backed by the real app — no DB, 401 on all protected endpoints."""
    from civicsignals_api.config import get_settings
    from civicsignals_api.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as tc:
        yield tc
    get_settings.cache_clear()


def test_list_quality_samples_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/v1/recipes/quality-samples")
    assert resp.status_code == 401


def test_record_judgements_requires_auth(client: TestClient) -> None:
    sample_id = str(uuid.uuid4())
    resp = client.post(
        f"/api/v1/recipes/quality-samples/{sample_id}/judgements",
        json={"field_judgements": {"title": "correct"}, "reviewer": "qa@example.com"},
    )
    assert resp.status_code == 401


def test_list_recipe_accuracy_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/v1/recipes/quality-accuracy")
    assert resp.status_code == 401


def test_get_recipe_accuracy_requires_auth(client: TestClient) -> None:
    resp = client.get("/api/v1/recipes/quality-accuracy/some-recipe")
    assert resp.status_code == 401
