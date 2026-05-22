"""Unit tests for the per-recipe cadence + jitter logic (D4; doc 18 §3, §6).

These exercise the *pure* scheduler core — due-calculation, cron parsing, and
the deterministic jitter window — with an injectable base time and no DB / Redis /
Celery, so they're fast and deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from civicsignals_api.modules.ingestion import scheduler
from civicsignals_api.modules.recipes.schemas import FetchPolicy, Recipe
from civicsignals_api.modules.recipes.services import RecipeError


def _recipe(
    recipe_id: str = "r1",
    *,
    cron: str | None = "0 */2 * * *",
    jitter_seconds: float = 0.0,
    version: int = 1,
) -> Recipe:
    schedule = {"cron": cron} if cron is not None else {}
    return Recipe(
        recipe_id=recipe_id,
        connector="http_static",
        version=version,
        entity={"name": "X"},
        schedule=schedule,
        fetch=FetchPolicy(jitter_seconds=jitter_seconds),
    )


def test_cadence_resolves_cron_and_default_jitter() -> None:
    cadence = scheduler.cadence_for(_recipe(cron="*/5 * * * *", jitter_seconds=0))
    assert cadence.cron == "*/5 * * * *"
    # No fetch.jitter_seconds -> the default spread window.
    assert cadence.jitter_window == scheduler.DEFAULT_JITTER_WINDOW


def test_cadence_uses_recipe_jitter_when_set() -> None:
    cadence = scheduler.cadence_for(_recipe(jitter_seconds=42.0))
    assert cadence.jitter_window == 42.0


def test_cadence_defaults_cron_when_absent() -> None:
    cadence = scheduler.cadence_for(_recipe(cron=None))
    assert cadence.cron == scheduler.DEFAULT_CRON


def test_invalid_cron_raises() -> None:
    with pytest.raises(RecipeError):
        scheduler.cadence_for(_recipe(cron="not a cron expr"))


def test_jitter_offset_is_deterministic_and_in_bounds() -> None:
    window = 300.0
    a1 = scheduler.jitter_offset("recipe-a", window)
    a2 = scheduler.jitter_offset("recipe-a", window)
    b = scheduler.jitter_offset("recipe-b", window)
    # Stable for the same recipe across calls (so a recipe always lands in the
    # same slot tick to tick).
    assert a1 == a2
    # Different recipes get different offsets (that's what de-synchronizes them).
    assert a1 != b
    # Always within [0, window).
    for off in (a1, b):
        assert 0.0 <= off < window


def test_jitter_offset_zero_window() -> None:
    assert scheduler.jitter_offset("anything", 0.0) == 0.0


def test_next_run_at_is_next_cron_boundary_plus_jitter() -> None:
    cadence = scheduler.cadence_for(_recipe(cron="0 */2 * * *", jitter_seconds=120))
    base = datetime(2026, 5, 22, 9, 5, 0, tzinfo=UTC)
    nxt = scheduler.next_run_at(cadence, last_run_at=None, base=base)
    # Next even hour after 09:05 is 10:00, plus the recipe's deterministic offset.
    offset = scheduler.jitter_offset("r1", 120.0)
    expected = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC) + timedelta(seconds=offset)
    assert nxt == expected
    assert nxt.tzinfo is not None  # tz-aware


def test_due_when_now_past_next_run_at() -> None:
    cadence = scheduler.cadence_for(_recipe(cron="0 */2 * * *", jitter_seconds=0))
    target = datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC)
    # now strictly before target -> not due.
    assert not scheduler.is_due(
        cadence,
        last_run_at=datetime(2026, 5, 22, 8, 0, tzinfo=UTC),
        next_run_at_stored=target,
        now=datetime(2026, 5, 22, 9, 59, tzinfo=UTC),
    )
    # now == target -> due.
    assert scheduler.is_due(
        cadence,
        last_run_at=datetime(2026, 5, 22, 8, 0, tzinfo=UTC),
        next_run_at_stored=target,
        now=target,
    )
    # now after target -> due.
    assert scheduler.is_due(
        cadence,
        last_run_at=datetime(2026, 5, 22, 8, 0, tzinfo=UTC),
        next_run_at_stored=target,
        now=datetime(2026, 5, 22, 10, 1, tzinfo=UTC),
    )


def test_never_run_recipe_is_due_immediately() -> None:
    cadence = scheduler.cadence_for(_recipe(cron="0 */2 * * *"))
    # No last_run_at AND no stored next_run_at -> bootstrap on the first tick.
    assert scheduler.is_due(
        cadence,
        last_run_at=None,
        next_run_at_stored=None,
        now=datetime(2026, 5, 22, 9, 5, tzinfo=UTC),
    )


def test_evaluate_returns_due_and_advances_next_run() -> None:
    # An explicit jitter window so the expected next_run is deterministic without
    # the (also-deterministic) default-window offset clouding the boundary check.
    recipe = _recipe(cron="*/5 * * * *", jitter_seconds=30)
    now = datetime(2026, 5, 22, 9, 7, 0, tzinfo=UTC)
    decision = scheduler.evaluate(recipe, last_run_at=None, next_run_at_stored=None, now=now)
    assert decision.due is True
    # next_run_at is the next */5 boundary after now (09:10) + the recipe's jitter.
    offset = scheduler.jitter_offset("r1", 30.0)
    assert decision.next_run_at == datetime(2026, 5, 22, 9, 10, 0, tzinfo=UTC) + timedelta(
        seconds=offset
    )


def test_evaluate_not_due_before_next_run() -> None:
    recipe = _recipe(cron="0 */2 * * *", jitter_seconds=0)
    decision = scheduler.evaluate(
        recipe,
        last_run_at=datetime(2026, 5, 22, 8, 0, tzinfo=UTC),
        next_run_at_stored=datetime(2026, 5, 22, 10, 0, tzinfo=UTC),
        now=datetime(2026, 5, 22, 9, 30, tzinfo=UTC),
    )
    assert decision.due is False


def test_naive_datetime_treated_as_utc() -> None:
    cadence = scheduler.cadence_for(_recipe(cron="0 */2 * * *", jitter_seconds=0))
    # A naive stored next_run_at must not crash the aware/naive comparison
    # (the scheduler treats a naive datetime as already-UTC).
    naive_last = datetime(2026, 5, 22, 8, 0)
    naive_next = datetime(2026, 5, 22, 10, 0)
    assert scheduler.is_due(
        cadence,
        last_run_at=naive_last,
        next_run_at_stored=naive_next,
        now=datetime(2026, 5, 22, 10, 1, tzinfo=UTC),
    )
