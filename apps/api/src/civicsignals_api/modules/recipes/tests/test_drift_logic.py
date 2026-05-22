"""Pure-logic tests for E7 recipe drift detection (doc 18 §3.2).

These exercise the rolling-window math, the auto-pause threshold decision, the
LLM-fallback alert, the failing-input sanitizer, and the run-outcome builder —
none of which need a database, so they run in every CI job (mirroring how the
runner / pipeline-stage logic is unit-tested without Postgres). The DB-backed
service path (record/compute/pause/evaluate against a live table) is covered by
``test_drift_persistence.py``, which is DSN-gated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from civicsignals_api.modules.recipes import drift
from civicsignals_api.modules.recipes.schemas import (
    DriftCounters,
    ExtractedDocument,
    ExtractionMethod,
    RollingMetrics,
)

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _row(
    *,
    age_hours: float,
    extractions_total: int = 1,
    extractions_succeeded: int = 1,
    signals_produced: int = 1,
    llm_fallbacks: int = 0,
    fields_total: int = 0,
    dead_letters: int = 0,
    wall_clock_seconds: float | None = None,
) -> drift._RunRow:
    return drift._RunRow(
        finished_at=NOW - timedelta(hours=age_hours),
        documents_total=extractions_total,
        extractions_total=extractions_total,
        extractions_succeeded=extractions_succeeded,
        signals_produced=signals_produced,
        llm_fallbacks=llm_fallbacks,
        fields_total=fields_total,
        dead_letters=dead_letters,
        wall_clock_seconds=wall_clock_seconds,
    )


# ---------------------------------------------------------------------------
# Rolling-window math: runs in / out of the window.
# ---------------------------------------------------------------------------


def test_rollup_excludes_runs_outside_window() -> None:
    rows = [
        _row(age_hours=1, extractions_total=2, extractions_succeeded=2, signals_produced=3),
        _row(age_hours=23, extractions_total=2, extractions_succeeded=1, signals_produced=1),
        # 25h ago — outside the 24h window, must be excluded.
        _row(age_hours=25, extractions_total=10, extractions_succeeded=0, signals_produced=0),
    ]
    m = drift.rollup_metrics("r", rows, window_hours=24.0, now=NOW)
    assert m.runs == 2
    assert m.extractions_total == 4
    assert m.extractions_succeeded == 3
    assert m.signals_produced == 4
    assert m.extraction_success_rate == 0.75
    assert m.avg_signals_per_run == 2.0


def test_rollup_boundary_run_at_cutoff_is_excluded() -> None:
    # A run exactly at the cutoff (window edge) is excluded (half-open interval).
    rows = [_row(age_hours=24.0, extractions_total=1, extractions_succeeded=1)]
    m = drift.rollup_metrics("r", rows, window_hours=24.0, now=NOW)
    assert m.runs == 0
    assert m.extraction_success_rate is None


def test_rollup_future_run_is_excluded() -> None:
    rows = [_row(age_hours=-1, extractions_total=1, extractions_succeeded=1)]
    m = drift.rollup_metrics("r", rows, window_hours=24.0, now=NOW)
    assert m.runs == 0


def test_rollup_empty_window_yields_none_rates_not_zero() -> None:
    m = drift.rollup_metrics("r", [], window_hours=24.0, now=NOW)
    assert m.runs == 0
    assert m.extraction_success_rate is None
    assert m.llm_fallback_rate is None
    assert m.avg_signals_per_run is None
    assert m.avg_wall_clock_seconds is None


def test_rollup_7d_window_includes_older_runs() -> None:
    rows = [
        _row(age_hours=1),
        _row(age_hours=100),  # ~4 days — inside 7d, outside 24h
    ]
    m24 = drift.rollup_metrics("r", rows, window_hours=drift.WINDOW_24H_HOURS, now=NOW)
    m7 = drift.rollup_metrics("r", rows, window_hours=drift.WINDOW_7D_HOURS, now=NOW)
    assert m24.runs == 1
    assert m7.runs == 2


def test_rollup_llm_fallback_rate_and_avg_wall_clock() -> None:
    rows = [
        _row(age_hours=1, fields_total=10, llm_fallbacks=2, wall_clock_seconds=4.0),
        _row(age_hours=2, fields_total=10, llm_fallbacks=4, wall_clock_seconds=6.0),
        # No wall clock recorded — must not pull the average toward zero.
        _row(age_hours=3, fields_total=0, llm_fallbacks=0, wall_clock_seconds=None),
    ]
    m = drift.rollup_metrics("r", rows, window_hours=24.0, now=NOW)
    assert m.fields_total == 20
    assert m.llm_fallbacks == 6
    assert m.llm_fallback_rate == 0.3
    assert m.avg_wall_clock_seconds == 5.0  # (4+6)/2, the null run excluded


# ---------------------------------------------------------------------------
# Auto-pause threshold decision.
# ---------------------------------------------------------------------------


def _metrics(*, rate: float | None, runs: int, llm_rate: float | None = None) -> RollingMetrics:
    return RollingMetrics(
        recipe_id="r",
        window_hours=24.0,
        runs=runs,
        extraction_success_rate=rate,
        llm_fallback_rate=llm_rate,
    )


def test_should_auto_pause_below_threshold() -> None:
    assert drift.should_auto_pause(_metrics(rate=0.3, runs=5), threshold=0.5, min_runs=3)


def test_should_not_auto_pause_at_or_above_threshold() -> None:
    assert not drift.should_auto_pause(_metrics(rate=0.5, runs=5), threshold=0.5, min_runs=3)
    assert not drift.should_auto_pause(_metrics(rate=0.9, runs=5), threshold=0.5, min_runs=3)


def test_should_not_auto_pause_too_few_runs() -> None:
    # Below threshold but only 2 runs (< min_runs=3): noise guard, no pause.
    assert not drift.should_auto_pause(_metrics(rate=0.0, runs=2), threshold=0.5, min_runs=3)


def test_should_not_auto_pause_when_rate_unmeasured() -> None:
    assert not drift.should_auto_pause(_metrics(rate=None, runs=5), threshold=0.5, min_runs=3)


def test_llm_fallback_alert_threshold() -> None:
    assert drift.llm_fallback_alerting(_metrics(rate=1.0, runs=3, llm_rate=0.25), threshold=0.2)
    assert not drift.llm_fallback_alerting(_metrics(rate=1.0, runs=3, llm_rate=0.2), threshold=0.2)
    assert not drift.llm_fallback_alerting(_metrics(rate=1.0, runs=3, llm_rate=None), threshold=0.2)


# ---------------------------------------------------------------------------
# Failing-input sanitizer.
# ---------------------------------------------------------------------------


def test_sanitize_sample_truncates_long_input() -> None:
    out = drift.sanitize_sample("x" * 5000, max_chars=100)
    assert out.startswith("x" * 100)
    assert "truncated" in out


def test_sanitize_sample_neutralizes_code_fence() -> None:
    out = drift.sanitize_sample("```js\nalert(1)\n```")
    assert "```" not in out


def test_sanitize_sample_handles_empty() -> None:
    assert "no sample" in drift.sanitize_sample(None)
    assert "no sample" in drift.sanitize_sample("")


# ---------------------------------------------------------------------------
# Run-outcome builder (folds D11 DriftCounters from per-document extractions).
# ---------------------------------------------------------------------------


def _extracted(
    method: ExtractionMethod, *, llm: int = 0, fields: int = 0, dl: int = 0
) -> ExtractedDocument:
    return ExtractedDocument(
        recipe_id="r",
        recipe_version=2,
        extraction_method=method,
        drift=DriftCounters(llm_fallbacks=llm, fields_total=fields, dead_letters=dl),
    )


def test_build_run_outcome_counts_successes_and_dead_letters() -> None:
    docs = [
        _extracted(ExtractionMethod.PRIMARY, fields=4),
        _extracted(ExtractionMethod.FALLBACK, fields=4),  # degraded still counts as success
        _extracted(ExtractionMethod.LLM_ASSISTED, llm=1, fields=4),
        _extracted(ExtractionMethod.DEAD_LETTER, dl=1, fields=4),  # the only miss
    ]
    outcome = drift.build_run_outcome("r", docs, recipe_version=2)
    assert outcome.recipe_id == "r"
    assert outcome.recipe_version == 2
    assert outcome.documents_total == 4
    assert outcome.extractions_total == 4
    assert outcome.extractions_succeeded == 3
    assert outcome.llm_fallbacks == 1
    assert outcome.fields_total == 16
    assert outcome.dead_letters == 1
    # signals_produced defaults to the success count when not supplied.
    assert outcome.signals_produced == 3


def test_build_run_outcome_signals_override() -> None:
    docs = [_extracted(ExtractionMethod.PRIMARY, fields=2)]
    outcome = drift.build_run_outcome("r", docs, signals_produced=7, wall_clock_seconds=1.5)
    assert outcome.signals_produced == 7
    assert outcome.wall_clock_seconds == 1.5


def test_build_run_outcome_empty_run() -> None:
    outcome = drift.build_run_outcome("r", [])
    assert outcome.documents_total == 0
    assert outcome.extractions_succeeded == 0
    assert outcome.signals_produced == 0
