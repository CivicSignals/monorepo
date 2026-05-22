"""Recipe scorecard aggregation — per-recipe quality dashboard (doc 19 §12.4, E12).

A **scorecard** summarises the health/accuracy of one recipe in a single object
so operators can see at a glance which recipes are degrading. It is built
entirely from the E7 data (``recipes_run_metric`` + ``recipes_drift_state``) —
no new tables, no recomputation of drift detection.

Design:
- :func:`build_scorecard` is the pure aggregation: given pre-loaded rolling
  metrics + drift-state it returns a :class:`RecipeScorecard`.  No DB calls,
  so it is unit-testable without Postgres.
- :func:`get_scorecard` is the single-recipe DB path: load metrics + state,
  delegate to :func:`build_scorecard`.
- :func:`list_scorecards` is the list path: enumerate recipe ids that have
  ever recorded a run (plus any that have a drift state but no runs), paginate
  cursor-style, compute each scorecard.  Filter by :class:`ScorecardHealth`.

The health roll-up:
  ``paused``   — drift_paused is True (auto-paused by E7)
  ``degraded`` — extraction_success_rate < DEGRADED_THRESHOLD over 24h
                  OR llm_fallback_rate > LLM_ALERT_THRESHOLD over 7d
                  (same thresholds as drift.should_auto_pause/llm_fallback_alerting
                  but we report degraded even below the auto-pause min_runs floor)
  ``healthy``  — anything else with at least one run recorded
  ``unknown``  — no runs at all (recipe exists in drift state only, or brand new)
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .drift import (
    WINDOW_7D_HOURS,
    WINDOW_24H_HOURS,
    compute_rolling_metrics,
    get_drift_state,
)
from .models import DriftState, RunMetric
from .schemas import DriftStateRecord, RollingMetrics

# Health thresholds — mirror drift.py's defaults so the dashboard label matches
# what auto-pause would decide (but we apply them regardless of min_runs).
_DEGRADED_SUCCESS_THRESHOLD = 0.7  # below 70% success → degraded
_LLM_ALERT_THRESHOLD = 0.2  # above 20% LLM fallback → degraded

# Pagination
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


class ScorecardHealth(StrEnum):
    """Rolled-up health status shown on the quality dashboard."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    PAUSED = "paused"
    UNKNOWN = "unknown"  # no run metrics recorded yet


class RecipeScorecard:
    """Computed quality scorecard for one recipe (E12).

    All fields are read-only; instances are produced by :func:`build_scorecard`.
    """

    __slots__ = (
        "computed_at",
        "drift_issue_url",
        "drift_paused",
        "health",
        "last_run_at",
        "paused_at",
        "paused_reason",
        "recipe_id",
        "window_7d",
        "window_24h",
    )

    def __init__(
        self,
        *,
        recipe_id: str,
        health: ScorecardHealth,
        drift_paused: bool,
        paused_reason: str | None,
        paused_at: datetime | None,
        drift_issue_url: str | None,
        last_run_at: datetime | None,
        window_24h: RollingMetrics,
        window_7d: RollingMetrics,
        computed_at: datetime,
    ) -> None:
        self.recipe_id = recipe_id
        self.health = health
        self.drift_paused = drift_paused
        self.paused_reason = paused_reason
        self.paused_at = paused_at
        self.drift_issue_url = drift_issue_url
        self.last_run_at = last_run_at
        self.window_24h = window_24h
        self.window_7d = window_7d
        self.computed_at = computed_at


# ---------------------------------------------------------------------------
# Pure aggregation (no DB) — unit-testable without Postgres
# ---------------------------------------------------------------------------


def _compute_health(
    *,
    drift_paused: bool,
    window_24h: RollingMetrics,
    window_7d: RollingMetrics,
) -> ScorecardHealth:
    """Roll up per-window metrics into a single health label."""
    if drift_paused:
        return ScorecardHealth.PAUSED
    # No runs at all → unknown
    if window_24h.runs == 0 and window_7d.runs == 0:
        return ScorecardHealth.UNKNOWN
    # A recorded success-rate below the threshold is degraded (regardless of
    # auto-pause min_runs — this is a dashboard label, not an auto-pause decision).
    rate_24h = window_24h.extraction_success_rate
    if rate_24h is not None and rate_24h < _DEGRADED_SUCCESS_THRESHOLD:
        return ScorecardHealth.DEGRADED
    llm_rate_7d = window_7d.llm_fallback_rate
    if llm_rate_7d is not None and llm_rate_7d > _LLM_ALERT_THRESHOLD:
        return ScorecardHealth.DEGRADED
    return ScorecardHealth.HEALTHY


def build_scorecard(
    recipe_id: str,
    *,
    window_24h: RollingMetrics,
    window_7d: RollingMetrics,
    drift_state: DriftStateRecord | None,
    last_run_at: datetime | None,
    now: datetime,
) -> RecipeScorecard:
    """Aggregate pre-loaded metrics + drift state into a :class:`RecipeScorecard`.

    Pure (no DB) — the DB reads are done by the callers (:func:`get_scorecard`,
    :func:`list_scorecards`) and injected here so unit tests can call this
    directly with fixtures.
    """
    drift_paused = drift_state.drift_paused if drift_state is not None else False
    paused_reason = drift_state.paused_reason if drift_state is not None else None
    paused_at = drift_state.paused_at if drift_state is not None else None
    drift_issue_url = drift_state.drift_issue_url if drift_state is not None else None

    health = _compute_health(
        drift_paused=drift_paused,
        window_24h=window_24h,
        window_7d=window_7d,
    )
    return RecipeScorecard(
        recipe_id=recipe_id,
        health=health,
        drift_paused=drift_paused,
        paused_reason=paused_reason,
        paused_at=paused_at,
        drift_issue_url=drift_issue_url,
        last_run_at=last_run_at,
        window_24h=window_24h,
        window_7d=window_7d,
        computed_at=now,
    )


# ---------------------------------------------------------------------------
# Empty-window sentinels (when a recipe has a drift state but no run metrics)
# ---------------------------------------------------------------------------


def _empty_metrics(recipe_id: str, window_hours: float) -> RollingMetrics:
    return RollingMetrics(recipe_id=recipe_id, window_hours=window_hours)


# ---------------------------------------------------------------------------
# DB-backed single-recipe path
# ---------------------------------------------------------------------------


async def get_scorecard(
    session: AsyncSession,
    recipe_id: str,
    *,
    now: datetime | None = None,
) -> RecipeScorecard:
    """Compute and return a scorecard for ``recipe_id``.

    Returns a scorecard even when the recipe has no recorded runs (all metrics
    will be zero, health will be ``unknown`` unless it has a drift state).
    """
    now = now or datetime.now(UTC)
    window_24h = await compute_rolling_metrics(
        session, recipe_id, window_hours=WINDOW_24H_HOURS, now=now
    )
    window_7d = await compute_rolling_metrics(
        session, recipe_id, window_hours=WINDOW_7D_HOURS, now=now
    )
    drift_state = await get_drift_state(session, recipe_id)
    last_run_at = await _get_last_run_at(session, recipe_id)
    return build_scorecard(
        recipe_id,
        window_24h=window_24h,
        window_7d=window_7d,
        drift_state=drift_state,
        last_run_at=last_run_at,
        now=now,
    )


async def _get_last_run_at(session: AsyncSession, recipe_id: str) -> datetime | None:
    """Return the most recent ``finished_at`` for ``recipe_id``, or ``None``."""
    stmt = (
        select(RunMetric.finished_at)
        .where(RunMetric.recipe_id == recipe_id)
        .order_by(RunMetric.finished_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Cursor helpers (keyset on recipe_id — alphabetically sorted strings)
# ---------------------------------------------------------------------------


def encode_cursor(recipe_id: str) -> str:
    """Encode a recipe_id string as an opaque URL-safe base64 cursor."""
    return base64.urlsafe_b64encode(recipe_id.encode()).decode("ascii")


def decode_cursor(cursor: str) -> str:
    """Decode an opaque cursor back to a recipe_id string, or raise ValueError."""
    try:
        return base64.urlsafe_b64decode(cursor.encode("ascii")).decode()
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# List path: enumerate all recipe ids with scorecard data
# ---------------------------------------------------------------------------


class ScorecardPage:
    """A cursor-paginated page of recipe scorecards."""

    __slots__ = ("items", "next_cursor")

    def __init__(self, items: list[RecipeScorecard], next_cursor: str | None) -> None:
        self.items = items
        self.next_cursor = next_cursor


async def _all_recipe_ids_with_data(session: AsyncSession) -> list[str]:
    """Collect the distinct recipe ids that have run metrics or a drift state.

    The union of:
    - all recipe_ids that ever recorded a RunMetric row
    - all recipe_ids that have a DriftState row (paused without a recent run)

    Returns the list sorted alphabetically for stable cursor pagination.
    """
    run_ids_rows = await session.execute(select(RunMetric.recipe_id).distinct())
    run_ids = set(run_ids_rows.scalars().all())

    drift_ids_rows = await session.execute(select(DriftState.recipe_id).distinct())
    drift_ids = set(drift_ids_rows.scalars().all())

    all_ids = run_ids | drift_ids
    return sorted(all_ids)


async def list_scorecards(
    session: AsyncSession,
    *,
    health: ScorecardHealth | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> ScorecardPage:
    """Return a cursor-paginated list of recipe scorecards.

    Pagination is keyset over ``recipe_id`` (alphabetical).  ``health`` filters
    the result to scorecards with that :class:`ScorecardHealth` label.

    Because recipe ids are plain strings (not DB-generated UUIDs) and the total
    population is typically small (hundreds to low thousands), we enumerate all
    ids once, compute scorecards in-memory in batches, and page through the sorted
    list.  This is simple and correct; a recipe table (doc 18 §3.5 "recipe
    registry") would allow a smarter SQL approach when the set grows large.
    """
    now = now or datetime.now(UTC)
    limit = max(1, min(limit, MAX_LIMIT))

    all_ids = await _all_recipe_ids_with_data(session)

    # Apply cursor: skip everything up to (and including) the cursor recipe_id.
    if cursor is not None:
        cursor_id = decode_cursor(cursor)
        all_ids = [rid for rid in all_ids if rid > cursor_id]

    # We may need to over-fetch when filtering by health (since health is not a
    # DB column).  Compute scorecards in batches and collect until we have
    # limit + 1 results (the +1 tells us if there is a next page).
    collected: list[RecipeScorecard] = []
    # Pre-load all drift states and last_run_at in a single pass for efficiency.
    drift_states = await _batch_load_drift_states(session, all_ids)
    last_runs = await _batch_load_last_runs(session, all_ids)

    # We need 7d of run metrics for each id — batch them.
    for recipe_id in all_ids:
        if len(collected) > limit:
            break
        w24, w7 = await _compute_both_windows(session, recipe_id, now=now)
        card = build_scorecard(
            recipe_id,
            window_24h=w24,
            window_7d=w7,
            drift_state=drift_states.get(recipe_id),
            last_run_at=last_runs.get(recipe_id),
            now=now,
        )
        if health is None or card.health == health:
            collected.append(card)

    # Page boundary: collected is limit+1 if there is a next page.
    if len(collected) > limit:
        page_items = collected[:limit]
        next_cursor = encode_cursor(page_items[-1].recipe_id)
    else:
        page_items = collected
        next_cursor = None

    return ScorecardPage(items=page_items, next_cursor=next_cursor)


async def _batch_load_drift_states(
    session: AsyncSession, recipe_ids: list[str]
) -> dict[str, DriftStateRecord]:
    """Load drift states for the given recipe ids in one query."""
    if not recipe_ids:
        return {}
    from .drift import _state_to_schema  # local import to avoid circular

    stmt = select(DriftState).where(DriftState.recipe_id.in_(recipe_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return {row.recipe_id: _state_to_schema(row) for row in rows}


async def _batch_load_last_runs(
    session: AsyncSession, recipe_ids: list[str]
) -> dict[str, datetime]:
    """Load the most recent finished_at per recipe in one query (window function)."""
    if not recipe_ids:
        return {}
    from sqlalchemy import func as sa_func  # noqa: F401 — used in expr

    # Use a subquery with DISTINCT ON (recipe_id) ORDER BY finished_at DESC.
    # SQLAlchemy 2.0 subquery approach:
    from sqlalchemy import text

    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (recipe_id) recipe_id, finished_at
            FROM recipes_run_metric
            WHERE recipe_id = ANY(:ids)
            ORDER BY recipe_id, finished_at DESC
            """
        ),
        {"ids": recipe_ids},
    )
    return {row.recipe_id: row.finished_at for row in result}


async def _compute_both_windows(
    session: AsyncSession,
    recipe_id: str,
    *,
    now: datetime,
) -> tuple[RollingMetrics, RollingMetrics]:
    """Compute the 24h and 7d rolling windows for one recipe."""
    w24 = await compute_rolling_metrics(
        session, recipe_id, window_hours=WINDOW_24H_HOURS, now=now
    )
    w7 = await compute_rolling_metrics(
        session, recipe_id, window_hours=WINDOW_7D_HOURS, now=now
    )
    return w24, w7


# ---------------------------------------------------------------------------
# Workspace-scoping note
# ---------------------------------------------------------------------------
# Recipe ids are workspace-agnostic strings (slugs) in the current model —
# ``RunMetric`` and ``DriftState`` carry only the slug (doc 18 §3.5). Until a
# recipe-registry table (doc 18 §3.5 TODO) maps recipe_id → workspace_id, the
# scorecard endpoints accept an ``X-Workspace-Id`` header for auth / membership
# verification (so only authenticated members can call them) but cannot filter
# by workspace at the DB level — they return scores for all recipes. This is the
# correct interim behaviour: the endpoints are workspace-authenticated but the
# data is global, exactly matching the current state of the recipes module.
# When the recipe registry lands it will add workspace_id to RunMetric/DriftState
# (or the registry table) and the filter can be added without changing the API shape.

_UUID_SENTINEL: uuid.UUID = uuid.UUID(int=0)  # unused; kept as a reminder of the FK gap
