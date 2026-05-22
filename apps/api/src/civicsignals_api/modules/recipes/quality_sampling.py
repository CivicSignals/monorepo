"""Extraction quality sampling — QA-7.

A repeatable **quality sampling process** for extraction accuracy:

- **Weekly beat task** (``recipes.sample_extraction_quality``): selects ~N random
  recent ``signals_signal`` rows, creates a ``recipes_quality_sample`` row per
  signal for human review. Idempotent per ISO-week window — a second run in the
  same week does not double-sample.
- **Per-field accuracy recording**: service functions to list pending samples and
  record per-field correct/incorrect judgements, computing an overall accuracy
  verdict per sample.
- **Per-recipe rollup**: aggregate recorded sample accuracy by ``recipe_id`` →
  field-level + overall accuracy %, sample count, and window — the deliverable that
  feeds recipe quality tracking over time.

Design notes:

- The ``recipes_quality_sample`` table lives in the ``recipes`` module (table
  prefix ``recipes_``) because recipe-accuracy tracking is that module's domain
  (doc 06 §3). The only cross-module coupling is a *loose string reference* to
  ``signals_signal.id`` — no FK across the module boundary (doc 06 §3 forbids
  cross-module FKs). ``signal_id`` is stored as a UUID column without a foreign-key
  constraint; the sampling query fetches signals via a raw SELECT.
- The sampler uses SQLAlchemy's ``text()`` to reach ``signals_signal`` for the
  random sample — not by importing the signals module's models. This keeps the
  module boundary clean.
- Per the QA-7 spec, ``field_judgements`` is a JSONB map ``{field_name: verdict}``
  where verdict ∈ ``"correct" | "incorrect" | "unknown"``. ``overall_verdict`` is
  derived: the fraction of non-unknown correct fields vs total non-unknown fields.
- All DB functions are async; the beat task is synchronous Celery (uses
  ``asyncio.run`` for the DB work, consistent with other beat tasks in this codebase).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.celery_app import celery_app
from civicsignals_api.config import get_settings

from .models import QualitySample

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults (overridable via Settings / env)
# ---------------------------------------------------------------------------

DEFAULT_SAMPLE_N = 50  # signals to sample per weekly window
DEFAULT_SIGNAL_WINDOW_DAYS = 30  # look back this many days for candidate signals

# Per-field verdict constants
VERDICT_CORRECT = "correct"
VERDICT_INCORRECT = "incorrect"
VERDICT_UNKNOWN = "unknown"

_VALID_VERDICTS = frozenset({VERDICT_CORRECT, VERDICT_INCORRECT, VERDICT_UNKNOWN})


# ---------------------------------------------------------------------------
# ISO-week window helpers
# ---------------------------------------------------------------------------


def _week_start(dt: datetime) -> datetime:
    """Return the Monday 00:00:00 UTC for the ISO week containing ``dt``."""
    # isocalendar().weekday is 1=Mon … 7=Sun
    day_of_week = dt.isocalendar().weekday - 1  # 0-based, 0=Mon
    monday = dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=day_of_week)
    return monday


def _week_label(dt: datetime) -> str:
    """Return the ISO week label for ``dt``, e.g. ``'2026-W21'``."""
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# ---------------------------------------------------------------------------
# Sampling DB functions
# ---------------------------------------------------------------------------


async def sample_extraction_quality(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    n: int = DEFAULT_SAMPLE_N,
    signal_window_days: int = DEFAULT_SIGNAL_WINDOW_DAYS,
) -> list[QualitySample]:
    """Select ~``n`` random recent signals and create ``recipes_quality_sample`` rows.

    Idempotent per ISO-week window: if rows already exist for this week's label
    the function returns the existing rows without inserting duplicates.

    The cross-module reach to ``signals_signal`` is via a raw SQL SELECT — we
    deliberately do *not* import the signals module's models (doc 06 §3 module
    boundary). The signal's ``recipe_id``, ``signal_type``, and ``details``
    (the extracted field-value snapshot) are captured at sample time so the sample
    row is self-contained even if the source signal is later updated or merged.

    Returns the (potentially pre-existing) sample rows for this week.
    """
    now = now or datetime.now(UTC)
    week_label = _week_label(now)

    # --- idempotency check ---------------------------------------------------
    existing_stmt = select(QualitySample).where(QualitySample.sample_window == week_label)
    existing = list((await session.execute(existing_stmt)).scalars().all())
    if existing:
        log.info(
            "quality_sampling.already_sampled",
            week_label=week_label,
            count=len(existing),
        )
        return existing

    # --- candidate signals: recent, not merged -------------------------------
    cutoff = now - timedelta(days=signal_window_days)
    # Fetch candidate ids + provenance from signals_signal via raw SQL
    # (no cross-module model import, doc 06 §3).
    candidates_sql = text(
        """
        SELECT
            id,
            recipe_id,
            signal_type,
            details,
            observed_at
        FROM signals_signal
        WHERE observed_at >= :cutoff
          AND status != 'merged'
        ORDER BY RANDOM()
        LIMIT :limit
        """
    )
    rows = (
        await session.execute(
            candidates_sql,
            {"cutoff": cutoff, "limit": n},
        )
    ).all()

    if not rows:
        log.info("quality_sampling.no_candidates", week_label=week_label)
        return []

    # --- create sample rows --------------------------------------------------
    sampled_at = now
    samples: list[QualitySample] = []
    for row in rows:
        sample = QualitySample(
            signal_id=row.id,
            recipe_id=row.recipe_id,
            signal_type=row.signal_type,
            field_snapshot=row.details if row.details is not None else {},
            sample_window=week_label,
            sampled_at=sampled_at,
        )
        session.add(sample)
        samples.append(sample)

    await session.flush()
    log.info(
        "quality_sampling.sampled",
        week_label=week_label,
        count=len(samples),
    )
    return samples


# ---------------------------------------------------------------------------
# Listing pending samples
# ---------------------------------------------------------------------------


async def list_pending_samples(
    session: AsyncSession,
    *,
    recipe_id: str | None = None,
    window: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[QualitySample]:
    """Return quality samples that have not yet been reviewed (``reviewed_at`` is NULL).

    Optionally filter by ``recipe_id`` and/or ``sample_window`` (ISO-week label).
    """
    stmt = (
        select(QualitySample)
        .where(QualitySample.reviewed_at.is_(None))
        .order_by(QualitySample.sampled_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if recipe_id is not None:
        stmt = stmt.where(QualitySample.recipe_id == recipe_id)
    if window is not None:
        stmt = stmt.where(QualitySample.sample_window == window)
    return list((await session.execute(stmt)).scalars().all())


async def list_all_samples(
    session: AsyncSession,
    *,
    recipe_id: str | None = None,
    window: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[QualitySample]:
    """Return all quality samples (pending and reviewed), with optional filters."""
    stmt = (
        select(QualitySample).order_by(QualitySample.sampled_at.desc()).limit(limit).offset(offset)
    )
    if recipe_id is not None:
        stmt = stmt.where(QualitySample.recipe_id == recipe_id)
    if window is not None:
        stmt = stmt.where(QualitySample.sample_window == window)
    return list((await session.execute(stmt)).scalars().all())


async def get_sample(session: AsyncSession, sample_id: uuid.UUID) -> QualitySample | None:
    """Fetch one quality sample by id, or return None."""
    return await session.get(QualitySample, sample_id)


# ---------------------------------------------------------------------------
# Recording per-field accuracy
# ---------------------------------------------------------------------------


class SampleNotFound(Exception):
    """Raised when a referenced quality sample does not exist."""


class InvalidVerdict(Exception):
    """Raised when a field verdict value is not one of the allowed strings."""


def _compute_overall_accuracy(judgements: dict[str, str]) -> float | None:
    """Compute overall accuracy from a ``{field → verdict}`` map.

    Only ``correct``/``incorrect`` fields count; ``unknown`` fields are excluded
    from both numerator and denominator. Returns ``None`` when every field is
    ``unknown`` (no judgeable data).
    """
    judged = [v for v in judgements.values() if v in (VERDICT_CORRECT, VERDICT_INCORRECT)]
    if not judged:
        return None
    correct = sum(1 for v in judged if v == VERDICT_CORRECT)
    return correct / len(judged)


async def record_field_accuracy(
    session: AsyncSession,
    sample_id: uuid.UUID,
    *,
    field_judgements: dict[str, str],
    reviewer: str,
    now: datetime | None = None,
) -> QualitySample:
    """Record per-field accuracy judgements for a quality sample.

    ``field_judgements`` maps field name → ``"correct" | "incorrect" | "unknown"``.
    Unknown fields are allowed (the reviewer skips fields they cannot assess).

    Raises :exc:`SampleNotFound` if the sample_id does not exist.
    Raises :exc:`InvalidVerdict` if any verdict value is not a valid string.
    """
    for field, verdict in field_judgements.items():
        if verdict not in _VALID_VERDICTS:
            raise InvalidVerdict(
                f"field {field!r}: verdict {verdict!r} must be one of {sorted(_VALID_VERDICTS)}"
            )

    sample = await get_sample(session, sample_id)
    if sample is None:
        raise SampleNotFound(f"quality sample {sample_id} not found")

    now = now or datetime.now(UTC)

    # Merge with any existing judgements (allow partial updates).
    merged: dict[str, str] = dict(sample.field_judgements or {})
    merged.update(field_judgements)

    recomputed = _compute_overall_accuracy(merged)

    stmt = (
        update(QualitySample)
        .where(QualitySample.id == sample_id)
        .values(
            field_judgements=merged,
            overall_accuracy=recomputed,
            reviewer=reviewer,
            reviewed_at=now,
        )
    )
    await session.execute(stmt)
    await session.refresh(sample)
    return sample


# ---------------------------------------------------------------------------
# Per-recipe accuracy rollup
# ---------------------------------------------------------------------------


class RecipeAccuracy:
    """Aggregated extraction accuracy for one recipe over recorded samples.

    Attributes:
        recipe_id: The recipe slug.
        sample_count: Total sample rows for this recipe (pending + reviewed).
        reviewed_count: Sample rows with at least one field judgement.
        overall_accuracy: Mean ``overall_accuracy`` across reviewed rows (``None``
            when no reviewed rows yet).
        field_accuracy: Per-field accuracy map ``{field_name: float}`` — the
            fraction of ``correct`` verdicts vs total judged (``correct`` +
            ``incorrect``) across all samples for that field. Fields with only
            ``unknown`` judgements are excluded.
        window: The ``sample_window`` filter applied (ISO-week label), or ``None``
            for all time.
    """

    __slots__ = (
        "field_accuracy",
        "overall_accuracy",
        "recipe_id",
        "reviewed_count",
        "sample_count",
        "window",
    )

    def __init__(
        self,
        recipe_id: str,
        *,
        sample_count: int,
        reviewed_count: int,
        overall_accuracy: float | None,
        field_accuracy: dict[str, float],
        window: str | None,
    ) -> None:
        self.recipe_id = recipe_id
        self.sample_count = sample_count
        self.reviewed_count = reviewed_count
        self.overall_accuracy = overall_accuracy
        self.field_accuracy = field_accuracy
        self.window = window


async def get_recipe_accuracy(
    session: AsyncSession,
    recipe_id: str,
    *,
    window: str | None = None,
) -> RecipeAccuracy:
    """Aggregate quality sample accuracy for ``recipe_id``.

    Optionally scoped to a single ISO-week ``window`` label (e.g. ``'2026-W21'``).
    Returns a :class:`RecipeAccuracy` even when no samples exist yet (all zeros /
    ``None``).
    """
    stmt = select(QualitySample).where(QualitySample.recipe_id == recipe_id)
    if window is not None:
        stmt = stmt.where(QualitySample.sample_window == window)
    rows = list((await session.execute(stmt)).scalars().all())

    sample_count = len(rows)
    reviewed_rows = [r for r in rows if r.reviewed_at is not None]
    reviewed_count = len(reviewed_rows)

    # Overall accuracy: mean of per-sample overall_accuracy (non-None).
    accuarcies = [r.overall_accuracy for r in reviewed_rows if r.overall_accuracy is not None]
    overall_accuracy: float | None = sum(accuarcies) / len(accuarcies) if accuarcies else None

    # Per-field accuracy: aggregate across all reviewed rows.
    field_correct: dict[str, int] = {}
    field_total: dict[str, int] = {}
    for row in reviewed_rows:
        for field, verdict in (row.field_judgements or {}).items():
            if verdict in (VERDICT_CORRECT, VERDICT_INCORRECT):
                field_total[field] = field_total.get(field, 0) + 1
                if verdict == VERDICT_CORRECT:
                    field_correct[field] = field_correct.get(field, 0) + 1

    field_accuracy = {
        field: field_correct.get(field, 0) / total
        for field, total in field_total.items()
        if total > 0
    }

    return RecipeAccuracy(
        recipe_id=recipe_id,
        sample_count=sample_count,
        reviewed_count=reviewed_count,
        overall_accuracy=overall_accuracy,
        field_accuracy=field_accuracy,
        window=window,
    )


async def list_recipe_accuracy(
    session: AsyncSession,
    *,
    window: str | None = None,
) -> list[RecipeAccuracy]:
    """Return per-recipe accuracy rollups for all recipes that have samples.

    Optionally scoped to a single ISO-week ``window`` label. Recipes with no
    samples are omitted from the result (unlike :func:`get_recipe_accuracy` which
    always returns a row).
    """
    stmt = select(QualitySample.recipe_id).distinct()
    if window is not None:
        stmt = stmt.where(QualitySample.sample_window == window)
    recipe_ids = list((await session.execute(stmt)).scalars().all())

    results: list[RecipeAccuracy] = []
    for recipe_id in sorted(recipe_ids):
        acc = await get_recipe_accuracy(session, recipe_id, window=window)
        results.append(acc)
    return results


# ---------------------------------------------------------------------------
# Celery beat task: weekly extraction quality sampling (QA-7)
# ---------------------------------------------------------------------------


@celery_app.task(name="recipes.sample_extraction_quality")
def sample_extraction_quality_task() -> dict[str, object]:
    """Weekly beat task: sample ~N signals for extraction quality review.

    Selects ``QUALITY_SAMPLE_N`` (default 50) random recent signals from
    ``signals_signal``, creates ``recipes_quality_sample`` rows for human review.
    Idempotent per ISO-week: a second run in the same week returns without
    inserting duplicates.

    Routed to the ``score`` worker (signals domain) via ``task_routes``
    (``recipes.*`` has no queue; we register this task under ``recipes.*`` and
    explicitly set queue to ``score`` — the worker that has read access to
    signal data). The beat schedule is set in ``celery_app.py``.
    """
    settings = get_settings()
    n = getattr(settings, "quality_sample_n", DEFAULT_SAMPLE_N)
    window_days = getattr(settings, "quality_sample_window_days", DEFAULT_SIGNAL_WINDOW_DAYS)

    async def _run() -> dict[str, object]:
        from civicsignals_api.db import SessionLocal as _SessionLocal

        async with _SessionLocal() as session, session.begin():
            samples = await sample_extraction_quality(
                session,
                n=n,
                signal_window_days=window_days,
            )
        return {"sampled": len(samples)}

    result = asyncio.run(_run())
    log.info("recipes.sample_extraction_quality.done", **result)
    return result
