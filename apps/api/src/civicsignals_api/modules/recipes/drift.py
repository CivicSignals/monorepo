"""Recipe drift detection + auto-pause + auto-issue (doc 18 §3.2, TODO E7).

This is the recipe-side of resilience layer §3.2: per-recipe rolling 24h/7d
metrics (extraction success rate, signals produced, LLM-fallback rate, wall
clock), an auto-pause when the rolling extraction success rate drops below a
configurable threshold, and an idempotent GitHub issue filed on auto-pause with a
sanitized sample of the failing input.

The metrics come from *recorded run outcomes* (``recipes_run_metric``), built from
the runner / extraction-pipeline result at completion (the canonical records + the
D11 :class:`DriftCounters`). We deliberately do **not** edit the pipeline stages
(E5/E9 own those) — :func:`record_run_outcome` is the seam the ingest/extract
completion path calls.

Module boundaries: recipes must **not** import ingestion (doc 06 §3), and the
*authoritative* scheduler pause flag (what the cadence dispatcher reads to skip a
recipe) lives in ingestion's ``ingestion_recipe_schedule.paused`` (D4). So
:func:`evaluate_recipe_drift` here returns a **decision** (``should_pause``) plus
records the recipes-side drift bookkeeping (``recipes_drift_state`` — issue
idempotency + the drift-pause record); the ingestion beat task
(``ingestion.evaluate_recipe_drift``) applies the authoritative pause via
``ingestion.services.set_recipe_paused``. **Past signals stay visible** — pausing
stops only *new* runs (doc 18 §3.2); ``clear_drift_state`` resets the bookkeeping
on unpause.

The pure rolling-window aggregation in :func:`rollup_metrics` is separated from the
DB read so it is unit-testable without a database (the bulk of the E7 test surface
is window math + threshold + idempotency, which need no Postgres).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import Settings, get_settings

from .github_client import GitHubClient
from .models import DriftState, RunMetric
from .schemas import (
    DriftEvaluation,
    DriftStateRecord,
    ExtractedDocument,
    ExtractionMethod,
    RollingMetrics,
    RunOutcome,
)

log = structlog.get_logger(__name__)

WINDOW_24H_HOURS = 24.0
WINDOW_7D_HOURS = 24.0 * 7.0

# Max sanitized characters of failing-input sample to embed in a GitHub issue,
# so a runaway document body doesn't bloat the issue (doc 18 §3.2 "a sample").
_ISSUE_SAMPLE_MAX_CHARS = 2000


@dataclass(frozen=True)
class _RunRow:
    """The fields of one recorded run the window math needs (DB-agnostic)."""

    finished_at: datetime
    documents_total: int
    extractions_total: int
    extractions_succeeded: int
    signals_produced: int
    llm_fallbacks: int
    fields_total: int
    dead_letters: int
    wall_clock_seconds: float | None


# ----------------------------------------------------------------------------
# Pure rolling-window math (no DB) — the heart of the E7 unit tests.
# ----------------------------------------------------------------------------


def rollup_metrics(
    recipe_id: str,
    rows: Iterable[_RunRow],
    *,
    window_hours: float,
    now: datetime,
) -> RollingMetrics:
    """Aggregate recorded runs into one window's :class:`RollingMetrics`.

    Only runs whose ``finished_at`` falls in ``(now - window, now]`` are counted —
    runs older than the window or (defensively) in the future are excluded. Rates
    return ``None`` rather than 0 when their denominator is empty so an idle window
    is never read as a 0% success rate (which would falsely trip auto-pause).
    """
    cutoff = now - timedelta(hours=window_hours)
    runs = 0
    documents_total = 0
    extractions_total = 0
    extractions_succeeded = 0
    signals_produced = 0
    llm_fallbacks = 0
    fields_total = 0
    dead_letters = 0
    wall_clock_sum = 0.0
    wall_clock_count = 0
    for row in rows:
        if row.finished_at <= cutoff or row.finished_at > now:
            continue
        runs += 1
        documents_total += row.documents_total
        extractions_total += row.extractions_total
        extractions_succeeded += row.extractions_succeeded
        signals_produced += row.signals_produced
        llm_fallbacks += row.llm_fallbacks
        fields_total += row.fields_total
        dead_letters += row.dead_letters
        if row.wall_clock_seconds is not None:
            wall_clock_sum += row.wall_clock_seconds
            wall_clock_count += 1

    extraction_success_rate = (
        extractions_succeeded / extractions_total if extractions_total > 0 else None
    )
    llm_fallback_rate = llm_fallbacks / fields_total if fields_total > 0 else None
    avg_signals_per_run = signals_produced / runs if runs > 0 else None
    avg_wall_clock_seconds = wall_clock_sum / wall_clock_count if wall_clock_count > 0 else None
    return RollingMetrics(
        recipe_id=recipe_id,
        window_hours=window_hours,
        runs=runs,
        documents_total=documents_total,
        extractions_total=extractions_total,
        extractions_succeeded=extractions_succeeded,
        signals_produced=signals_produced,
        llm_fallbacks=llm_fallbacks,
        fields_total=fields_total,
        dead_letters=dead_letters,
        extraction_success_rate=extraction_success_rate,
        llm_fallback_rate=llm_fallback_rate,
        avg_signals_per_run=avg_signals_per_run,
        avg_wall_clock_seconds=avg_wall_clock_seconds,
    )


def should_auto_pause(
    metrics: RollingMetrics,
    *,
    threshold: float,
    min_runs: int,
) -> bool:
    """Decide whether the 24h window warrants an auto-pause (doc 18 §3.2).

    Pause only when we have a measured extraction success rate (a non-empty
    extraction denominator), at least ``min_runs`` runs in the window (so a single
    bad run on a low-volume recipe doesn't trip — doc 18 §8), and the rate is
    strictly below ``threshold``.
    """
    if metrics.extraction_success_rate is None:
        return False
    if metrics.runs < min_runs:
        return False
    return metrics.extraction_success_rate < threshold


def llm_fallback_alerting(metrics: RollingMetrics, *, threshold: float) -> bool:
    """Whether the 7d LLM-fallback rate crossed the degradation alert threshold."""
    rate = metrics.llm_fallback_rate
    return rate is not None and rate > threshold


def sanitize_sample(sample: str | None, *, max_chars: int = _ISSUE_SAMPLE_MAX_CHARS) -> str:
    """Sanitize a failing-input sample for embedding in a public GitHub issue.

    The issue is filed on a public repo, so we (1) cap the length, and (2) fence it
    so the markdown renders it as a literal code block (no HTML/markdown injection
    from the source content). ``None``/empty yields a clear placeholder.
    """
    if not sample:
        return "(no sample input captured)"
    text = sample.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...(truncated)"
    # Neutralize any embedded code fence so the source can't break out of ours:
    # space out the three backticks so they no longer form a closing fence.
    text = text.replace("```", "` ` `")
    return text


# ----------------------------------------------------------------------------
# Run-outcome recording (the hook into run/pipeline completion).
# ----------------------------------------------------------------------------


def build_run_outcome(
    recipe_id: str,
    extractions: Iterable[ExtractedDocument],
    *,
    recipe_version: int = 1,
    signals_produced: int | None = None,
    wall_clock_seconds: float | None = None,
    finished_at: datetime | None = None,
) -> RunOutcome:
    """Aggregate a run's per-document extractions into a :class:`RunOutcome` (E7).

    The hook into run completion: given the :class:`ExtractedDocument` results a run
    produced (via ``RecipeRunner.run_pointers_collecting_extractions`` or the
    extraction pipeline), tally the drift metrics — *without* touching the pipeline
    stages. An extraction is a **success** unless its document-level method is
    ``dead_letter`` (every field missed); ``primary``/``fallback``/``llm_assisted``
    all count as success per doc 18 §3.2 (a degraded extraction still produced a
    signal). The LLM-fallback / fields-total / dead-letter counts come straight from
    each document's D11 :class:`DriftCounters`.

    ``signals_produced`` defaults to the number of successful extractions when not
    supplied (one canonical record per document in the runner's normalize step); a
    caller with the real canonical-record count should pass it.
    """
    documents_total = 0
    extractions_succeeded = 0
    llm_fallbacks = 0
    fields_total = 0
    dead_letters = 0
    for doc in extractions:
        documents_total += 1
        if doc.extraction_method != ExtractionMethod.DEAD_LETTER:
            extractions_succeeded += 1
        llm_fallbacks += doc.drift.llm_fallbacks
        fields_total += doc.drift.fields_total
        dead_letters += doc.drift.dead_letters
    return RunOutcome(
        recipe_id=recipe_id,
        recipe_version=recipe_version,
        documents_total=documents_total,
        extractions_total=documents_total,
        extractions_succeeded=extractions_succeeded,
        signals_produced=(
            signals_produced if signals_produced is not None else extractions_succeeded
        ),
        llm_fallbacks=llm_fallbacks,
        fields_total=fields_total,
        dead_letters=dead_letters,
        wall_clock_seconds=wall_clock_seconds,
        finished_at=finished_at,
    )


async def record_run_outcome(session: AsyncSession, outcome: RunOutcome) -> None:
    """Persist one run's outcome to ``recipes_run_metric`` (doc 18 §3.2, E7).

    The seam the ingest/extract completion path calls with the run result — *not*
    by editing the pipeline stages. ``finished_at`` defaults to now() when omitted.
    The caller owns the transaction (this flushes, not commits).
    """
    session.add(
        RunMetric(
            recipe_id=outcome.recipe_id,
            recipe_version=outcome.recipe_version,
            finished_at=outcome.finished_at or datetime.now(UTC),
            documents_total=outcome.documents_total,
            extractions_total=outcome.extractions_total,
            extractions_succeeded=outcome.extractions_succeeded,
            signals_produced=outcome.signals_produced,
            llm_fallbacks=outcome.llm_fallbacks,
            fields_total=outcome.fields_total,
            dead_letters=outcome.dead_letters,
            wall_clock_seconds=outcome.wall_clock_seconds,
        )
    )
    await session.flush()


async def _load_run_rows(
    session: AsyncSession, recipe_id: str, *, since: datetime
) -> list[_RunRow]:
    """Load recorded runs for ``recipe_id`` finished at/after ``since`` (index range)."""
    stmt = (
        select(
            RunMetric.finished_at,
            RunMetric.documents_total,
            RunMetric.extractions_total,
            RunMetric.extractions_succeeded,
            RunMetric.signals_produced,
            RunMetric.llm_fallbacks,
            RunMetric.fields_total,
            RunMetric.dead_letters,
            RunMetric.wall_clock_seconds,
        )
        .where(RunMetric.recipe_id == recipe_id, RunMetric.finished_at >= since)
        .order_by(RunMetric.finished_at)
    )
    rows = (await session.execute(stmt)).all()
    return [
        _RunRow(
            finished_at=r.finished_at,
            documents_total=r.documents_total,
            extractions_total=r.extractions_total,
            extractions_succeeded=r.extractions_succeeded,
            signals_produced=r.signals_produced,
            llm_fallbacks=r.llm_fallbacks,
            fields_total=r.fields_total,
            dead_letters=r.dead_letters,
            wall_clock_seconds=r.wall_clock_seconds,
        )
        for r in rows
    ]


async def compute_rolling_metrics(
    session: AsyncSession,
    recipe_id: str,
    *,
    window_hours: float,
    now: datetime | None = None,
) -> RollingMetrics:
    """Compute one recipe's rolling metrics over ``window_hours`` (doc 18 §3.2).

    Reads only the rows inside the window (an index range on
    ``(recipe_id, finished_at)``) then defers to the pure :func:`rollup_metrics`.
    """
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=window_hours)
    rows = await _load_run_rows(session, recipe_id, since=since)
    return rollup_metrics(recipe_id, rows, window_hours=window_hours, now=now)


# ----------------------------------------------------------------------------
# Drift state: the recipes module's drift bookkeeping (issue idempotency + the
# drift-pause record). NOTE: the *authoritative* scheduler pause flag lives in
# ingestion (``ingestion_recipe_schedule.paused``, D4) — recipes can't import
# ingestion (doc 06 §3), so ``evaluate_recipe_drift`` returns a *decision* and the
# ingestion beat task applies the pause via ``ingestion.services.set_recipe_paused``.
# ----------------------------------------------------------------------------


async def _get_or_create_state(session: AsyncSession, recipe_id: str) -> DriftState:
    """Get-or-create the per-recipe drift-state row (idempotent on recipe_id)."""
    stmt = (
        pg_insert(DriftState)
        .values(id=uuid.uuid4(), recipe_id=recipe_id, drift_paused=False)
        .on_conflict_do_nothing(constraint="recipes_drift_state_recipe_uq")
        .returning(DriftState.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is not None:
        row = await session.get(DriftState, inserted_id)
        assert row is not None
        return row
    existing = (
        await session.execute(select(DriftState).where(DriftState.recipe_id == recipe_id))
    ).scalar_one()
    return existing


async def get_drift_state(session: AsyncSession, recipe_id: str) -> DriftStateRecord | None:
    """Read a recipe's drift bookkeeping, or ``None`` if it has never drifted."""
    row = (
        await session.execute(select(DriftState).where(DriftState.recipe_id == recipe_id))
    ).scalar_one_or_none()
    return _state_to_schema(row) if row is not None else None


async def record_drift_pause(
    session: AsyncSession,
    recipe_id: str,
    *,
    reason: str,
    now: datetime | None = None,
) -> DriftStateRecord:
    """Record (in the recipes module) that drift decided to pause a recipe.

    This is *bookkeeping*, not the authoritative scheduler pause — the caller (the
    ingestion beat task) separately calls ``ingestion.services.set_recipe_paused``
    to actually stop the scheduler. Idempotent: re-recording an already-drift-paused
    recipe preserves ``paused_at`` / ``drift_issue_url`` (keeping the auto-issue
    idempotent) and only refreshes the reason. The caller owns the transaction.
    """
    now = now or datetime.now(UTC)
    state = await _get_or_create_state(session, recipe_id)
    if not state.drift_paused:
        state.drift_paused = True
        state.paused_at = now
    state.paused_reason = reason
    await session.flush()
    return _state_to_schema(state)


async def clear_drift_state(session: AsyncSession, recipe_id: str) -> DriftStateRecord:
    """Clear a recipe's drift bookkeeping (on manual unpause after a fix).

    Clears the recorded drift issue so the *next* breakage opens a fresh issue
    rather than being suppressed by the stale idempotency key. This does **not**
    unpause the scheduler — the caller pairs it with
    ``ingestion.services.set_recipe_paused(..., paused=False)``. Past signals were
    never removed, so resuming simply lets new runs flow again (doc 18 §3.2).
    """
    state = await _get_or_create_state(session, recipe_id)
    state.drift_paused = False
    state.paused_reason = None
    state.paused_at = None
    state.drift_issue_url = None
    await session.flush()
    return _state_to_schema(state)


def _state_to_schema(row: DriftState) -> DriftStateRecord:
    return DriftStateRecord(
        recipe_id=row.recipe_id,
        drift_paused=row.drift_paused,
        paused_reason=row.paused_reason,
        paused_at=row.paused_at,
        drift_issue_url=row.drift_issue_url,
    )


# ----------------------------------------------------------------------------
# The evaluation: metrics -> auto-pause -> idempotent GitHub issue.
# ----------------------------------------------------------------------------


def _build_issue(
    recipe_id: str,
    window_24h: RollingMetrics,
    window_7d: RollingMetrics,
    *,
    reason: str,
    failing_sample: str | None,
) -> tuple[str, str, list[str]]:
    """Compose the GitHub issue title/body/labels for an auto-paused recipe."""
    title = f"[recipe drift] auto-paused recipe `{recipe_id}`"
    rate = window_24h.extraction_success_rate
    rate_str = "n/a" if rate is None else f"{rate:.0%}"
    llm_rate = window_7d.llm_fallback_rate
    llm_str = "n/a" if llm_rate is None else f"{llm_rate:.0%}"
    sample = sanitize_sample(failing_sample)
    body = (
        f"Recipe `{recipe_id}` was **auto-paused** by drift detection "
        f"(doc 18 §3.2).\n\n"
        f"**Reason:** {reason}\n\n"
        f"## Rolling metrics (24h)\n"
        f"- runs: {window_24h.runs}\n"
        f"- extraction success rate: {rate_str} "
        f"({window_24h.extractions_succeeded}/{window_24h.extractions_total})\n"
        f"- signals produced: {window_24h.signals_produced}\n"
        f"- dead-letters: {window_24h.dead_letters}\n\n"
        f"## Rolling metrics (7d)\n"
        f"- LLM-fallback rate: {llm_str} "
        f"({window_7d.llm_fallbacks}/{window_7d.fields_total})\n"
        f"- avg wall clock: "
        f"{'n/a' if window_7d.avg_wall_clock_seconds is None else f'{window_7d.avg_wall_clock_seconds:.1f}s'}\n\n"
        f"## Sample failing input (sanitized)\n"
        f"```\n{sample}\n```\n\n"
        f"The recipe stays paused (new runs are skipped; past signals remain "
        f"visible) until a human inspects and fixes or unpauses it."
    )
    return title, body, ["recipe-drift", "auto-paused"]


async def evaluate_recipe_drift(
    session: AsyncSession,
    recipe_id: str,
    github_client: GitHubClient,
    *,
    already_paused: bool = False,
    settings: Settings | None = None,
    now: datetime | None = None,
    failing_sample: str | None = None,
) -> DriftEvaluation:
    """Recompute rolling metrics for one recipe and decide auto-pause + auto-issue.

    Pipeline (doc 18 §3.2):
      1. Compute the 24h + 7d rolling windows.
      2. If the 24h extraction success rate is below threshold (with enough runs),
         this is a drift pause: record the drift bookkeeping (``record_drift_pause``)
         and — if no issue is on file yet — open a GitHub issue with a sanitized
         sample + the metrics, recording its URL as the idempotency key. The
         returned ``should_pause`` tells the caller to flip the *authoritative*
         scheduler pause via ``ingestion.services.set_recipe_paused`` — this function
         does **not** touch the scheduler flag (recipes can't import ingestion).
      3. Flag a 7d LLM-fallback-rate breach for alerting.

    ``already_paused`` is the recipe's current authoritative scheduler-pause state,
    passed in by the ingestion beat task (which owns that flag). Issue-filing is
    keyed on the *current-metrics* drift verdict, not on ``already_paused``, so a
    recipe paused **manually** (not drifting) never gets a drift issue, while a
    drift-paused recipe whose issue-open failed retries on a later tick. Idempotent:
    a recipe with an issue already on file is not re-filed. The caller owns the
    transaction. ``github_client`` is injected (the no-op client when ``GITHUB_TOKEN``
    is unset).
    """
    settings = settings or get_settings()
    now = now or datetime.now(UTC)

    window_24h = await compute_rolling_metrics(
        session, recipe_id, window_hours=WINDOW_24H_HOURS, now=now
    )
    window_7d = await compute_rolling_metrics(
        session, recipe_id, window_hours=WINDOW_7D_HOURS, now=now
    )

    state = await get_drift_state(session, recipe_id)
    issue_already_filed = state.drift_issue_url if state is not None else None

    should_pause = should_auto_pause(
        window_24h,
        threshold=settings.drift_extraction_success_threshold,
        min_runs=settings.drift_min_runs_for_pause,
    )
    llm_alert = llm_fallback_alerting(window_7d, threshold=settings.drift_llm_fallback_threshold)

    issue_url = issue_already_filed
    pause_reason: str | None = state.paused_reason if state is not None else None

    if should_pause:
        rate = window_24h.extraction_success_rate
        rate_str = "n/a" if rate is None else f"{rate:.2f}"
        pause_reason = (
            f"extraction success {rate_str} "
            f"< {settings.drift_extraction_success_threshold:.2f} over 24h "
            f"({window_24h.runs} runs)"
        )
        # Record the recipes-side drift bookkeeping (not the scheduler flag).
        await record_drift_pause(session, recipe_id, reason=pause_reason, now=now)

    # File the auto-issue only for a **drift** pause (gate on ``should_pause``, the
    # current-metrics verdict — not on the manual scheduler flag) and only while no
    # issue is on file yet. A drift-paused recipe whose issue-open failed retries on
    # a later tick because ``should_pause`` stays true until the bad runs age out of
    # the 24h window. A no-op client returns None, so nothing is recorded and a later
    # tick (once configured) can still file it.
    if should_pause and issue_already_filed is None:
        title, body, labels = _build_issue(
            recipe_id,
            window_24h,
            window_7d,
            reason=pause_reason or "",
            failing_sample=failing_sample,
        )
        # The GitHub client is intentionally *sync* (usable from tests and any
        # non-async caller), but this runs inside the beat task's event loop, so a
        # real HTTP POST would block it. Offload to a worker thread so the loop stays
        # free; the no-op / recording fakes return immediately either way.
        #
        # Best-effort: filing the issue is a *notification*, not the pause itself.
        # A GitHub failure (non-2xx, network) must NOT lose the pause decision or
        # abort the tick — so swallow it and leave ``drift_issue_url`` unset, which
        # means the next drift tick retries the file (the idempotency guard only
        # suppresses once an issue actually exists).
        try:
            opened = await asyncio.to_thread(
                github_client.open_issue, title=title, body=body, labels=labels
            )
        except Exception:
            log.warning("recipes.drift.issue_open_failed", recipe_id=recipe_id, exc_info=True)
            opened = None
        if opened is not None:
            issue_url = opened
            row = await _get_or_create_state(session, recipe_id)
            row.drift_issue_url = opened
            await session.flush()

    return DriftEvaluation(
        recipe_id=recipe_id,
        window_24h=window_24h,
        window_7d=window_7d,
        should_pause=should_pause,
        newly_paused=should_pause and not already_paused,
        pause_reason=pause_reason,
        llm_fallback_alert=llm_alert,
        issue_url=issue_url,
    )


async def list_recipe_ids_with_runs(session: AsyncSession, *, since: datetime) -> list[str]:
    """Distinct recipe ids that recorded a run at/after ``since`` (beat-task scope).

    The drift beat task evaluates only recipes that actually ran recently — a
    recipe with no runs in the window has nothing to evaluate, and scanning every
    recipe id ever seen would not scale.
    """
    rows = (
        await session.execute(
            select(RunMetric.recipe_id).where(RunMetric.finished_at >= since).distinct()
        )
    ).scalars()
    return list(rows)


def evaluation_subjects(recipe_ids: Sequence[str]) -> list[str]:
    """De-duplicate + sort the recipe ids the beat task will evaluate (stable order)."""
    return sorted(set(recipe_ids))
