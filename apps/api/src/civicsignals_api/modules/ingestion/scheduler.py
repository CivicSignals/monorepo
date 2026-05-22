"""Per-recipe cadence + jitter logic for the ingestion scheduler (D4).

This module is the **pure** core of the scheduler — no Celery, no Redis, no DB.
It answers one question deterministically: *given a recipe's ``schedule.cron`` and
when it last ran, when is it next due, and is it due now?* The Celery beat task
(``ingestion.dispatch_due_recipes`` in ``tasks.py``) wires this to the
``ingestion_recipe_schedule`` run-state table, the Redis locks, and the
``ingestion.crawl_recipe`` enqueue. Keeping the math here (with an injectable base
time) makes due-calculation + jitter bounds unit-testable offline (doc 18 §3, §6).

Cron is evaluated with ``croniter`` (Celery's own ``crontab.is_due`` is wall-clock
coupled and awkward to drive from a custom dispatcher). A recipe with no
``schedule.cron`` defaults to :data:`DEFAULT_CRON` so a recipe is never silently
un-scheduled — an explicit cadence is preferred, but the absence of one should not
mean "never run".

Jitter (doc 18 §6.4): all recipes sharing a cron ("every 2 hours", on the hour)
would otherwise fire in lockstep and hammer their sources at the same instant. We
add a per-recipe, **deterministic** offset in ``[0, jitter_window]`` derived from
the recipe id, so the spread is stable across ticks (the same recipe always lands
in the same slot) yet differs between recipes. ``jitter_window`` defaults to the
recipe's own ``fetch.jitter_seconds`` when set, else :data:`DEFAULT_JITTER_WINDOW`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from croniter import croniter

from civicsignals_api.modules.recipes.services import (
    Recipe,
    RecipeError,
    list_recipe_ids,
    load_recipe,
)

# A recipe without an explicit cron still gets a sane cadence rather than never
# running. Hourly is the conservative default for a poll-driven source (doc 18 §1).
DEFAULT_CRON = "0 * * * *"
# When a recipe declares no fetch.jitter_seconds, spread same-cron recipes over a
# 5-minute window so a fleet of "on the hour" recipes doesn't fire simultaneously.
DEFAULT_JITTER_WINDOW = 300.0


def utcnow() -> datetime:
    """Clock seam: timezone-aware UTC now (overridden in tests with a fixed time)."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class RecipeCadence:
    """The resolved schedule inputs for one recipe (pure value object)."""

    recipe_id: str
    recipe_version: int
    cron: str
    jitter_window: float


def cadence_for(recipe: Recipe) -> RecipeCadence:
    """Resolve a recipe's cron + jitter window from its DSL fields."""
    cron = recipe.schedule.get("cron") or DEFAULT_CRON
    if not croniter.is_valid(cron):
        raise RecipeError(f"recipe {recipe.recipe_id!r} has an invalid cron {cron!r}")
    jitter = recipe.fetch.jitter_seconds
    jitter_window = jitter if jitter > 0 else DEFAULT_JITTER_WINDOW
    return RecipeCadence(
        recipe_id=recipe.recipe_id,
        recipe_version=recipe.version,
        cron=cron,
        jitter_window=jitter_window,
    )


def jitter_offset(recipe_id: str, jitter_window: float) -> float:
    """Deterministic per-recipe offset in ``[0, jitter_window)`` (doc 18 §6.4).

    Derived from a stable hash of the recipe id so the offset is the same on every
    tick (a recipe always lands in the same slot) but differs across recipes — that
    is what de-synchronizes a fleet of same-cron recipes. Returns 0.0 when the
    window is non-positive.
    """
    if jitter_window <= 0:
        return 0.0
    digest = hashlib.sha256(recipe_id.encode("utf-8")).digest()
    # Use 8 bytes as a fraction in [0, 1) then scale to the window.
    fraction = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return fraction * jitter_window


def next_run_at(
    cadence: RecipeCadence,
    *,
    last_run_at: datetime | None,
    base: datetime,
) -> datetime:
    """Compute the next due time: the next cron boundary after ``base`` + jitter.

    ``base`` is the reference time (the last run, or ``now`` for a never-run
    recipe). The deterministic per-recipe jitter offset is added to the cron
    boundary so same-cron recipes don't fire in lockstep. Returns a tz-aware UTC
    datetime. ``croniter`` operates in the timezone of the base it is given; we
    keep everything in UTC.
    """
    reference = last_run_at if last_run_at is not None else base
    reference = _as_utc(reference)
    boundary: datetime = croniter(cadence.cron, reference).get_next(datetime)
    offset = jitter_offset(cadence.recipe_id, cadence.jitter_window)
    return boundary + timedelta(seconds=offset)


def is_due(
    cadence: RecipeCadence,
    *,
    last_run_at: datetime | None,
    next_run_at_stored: datetime | None,
    now: datetime,
) -> bool:
    """Is this recipe due to run at ``now``?

    A recipe is due when ``now >= next_run_at``. We trust a stored ``next_run_at``
    when present (it already carries the jitter offset from a previous tick); a
    recipe that has never been scheduled (no stored ``next_run_at``) is due once
    its first computed boundary has passed — for a never-run recipe that means due
    immediately on the first tick (``croniter`` from ``now`` yields a *future*
    boundary, so we fall back to "never run -> due now").
    """
    if last_run_at is None and next_run_at_stored is None:
        # First-ever sighting: run on the next tick rather than waiting a whole
        # cron period to bootstrap a fresh deployment (doc 18 §6 — recipes start
        # producing as soon as they're registered).
        return True
    target = next_run_at_stored
    if target is None:
        target = next_run_at(cadence, last_run_at=last_run_at, base=now)
    return _as_utc(now) >= _as_utc(target)


@dataclass(frozen=True)
class DueDecision:
    """The dispatcher's per-recipe verdict for one tick."""

    cadence: RecipeCadence
    due: bool
    next_run_at: datetime


def evaluate(
    recipe: Recipe,
    *,
    last_run_at: datetime | None,
    next_run_at_stored: datetime | None,
    now: datetime,
) -> DueDecision:
    """Decide whether ``recipe`` is due now and what its next ``next_run_at`` is.

    Returns both the due verdict (for *this* tick) and the freshly computed
    ``next_run_at`` to persist *after* dispatching (advanced from ``now`` so the
    recipe isn't re-fired on the very next tick). The dispatcher writes the new
    ``next_run_at`` only when it actually enqueues the crawl.
    """
    cadence = cadence_for(recipe)
    due = is_due(
        cadence,
        last_run_at=last_run_at,
        next_run_at_stored=next_run_at_stored,
        now=now,
    )
    upcoming = next_run_at(cadence, last_run_at=now, base=now)
    return DueDecision(cadence=cadence, due=due, next_run_at=upcoming)


def active_recipe_ids() -> Sequence[str]:
    """All schedulable recipes: the YAML recipes under ``recipes/`` (doc 18 §3.5).

    There is no ``recipes_recipe`` table in MVP — recipes are versioned YAML — so
    the recipe set is the on-disk list. The dispatcher pairs each id with its
    ``ingestion_recipe_schedule`` run-state row (created on first sighting). When a
    DB-registered recipe source lands later, it's an additional source merged here.
    """
    return list_recipe_ids()


def load_active_recipes() -> list[Recipe]:
    """Load + validate every active recipe (skips ones that fail to parse).

    A single broken recipe must not stall the whole dispatcher tick, so a recipe
    that fails to load/validate is skipped (it will surface via drift/validation
    alerting, TODO E7) rather than raising out of the scan.
    """
    recipes: list[Recipe] = []
    for recipe_id in active_recipe_ids():
        try:
            recipes.append(load_recipe(recipe_id))
        except RecipeError:
            # Malformed recipe — skip; do not abort the whole tick.
            continue
    return recipes


def _as_utc(value: datetime) -> datetime:
    """Normalize to tz-aware UTC (treat a naive datetime as already-UTC)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


__all__ = [
    "DEFAULT_CRON",
    "DEFAULT_JITTER_WINDOW",
    "DueDecision",
    "RecipeCadence",
    "active_recipe_ids",
    "cadence_for",
    "evaluate",
    "is_due",
    "jitter_offset",
    "load_active_recipes",
    "next_run_at",
    "utcnow",
]
