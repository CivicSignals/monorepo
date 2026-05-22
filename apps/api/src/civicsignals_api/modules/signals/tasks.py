"""Celery tasks for the signals module (doc 06 §8)."""

from __future__ import annotations

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="signals.dedupe_recent")
def dedupe_recent() -> None:
    """Sweep recent signals for exact-key duplicates (doc 19 §7).

    Inline dedupe at store time (``signals.services.store_signal``, E5) already
    collapses duplicates as they arrive; this beat task is the backstop sweep that
    re-checks the recent window for duplicates that slipped past the inline path
    (e.g. a race between two workers storing the same key concurrently).

    # TODO E5+: implement the periodic sweep — group recent ``signals_signal`` rows
    # by ``(entity_id, signal_type, content_hash)`` within each type window and merge
    # any siblings via ``signals.dedupe.merge_signal``.
    """


@celery_app.task(name="signals.rescore_workspace")
def rescore_workspace(workspace_id: str) -> None:
    """Re-score a workspace after an ICP change (F6 backfill, doc 14 §7).

    The matcher + scorer F3 lands (``signals.services.score_workspace_candidates`` /
    ``score_signal_for_workspace``) is what this task body drives: pre-filter the
    historical signal window by the (new) ICP dimensions, full-score each candidate,
    and sparse-upsert ``signals_workspace_score`` rows (idempotent ON CONFLICT, doc 14
    §7.3). The live new-signal path already runs on ``signal.created`` (F3, see
    ``signals.listeners``).

    # TODO F6: implement the bounded backfill body — pull the lookback window via
    # ``signals.services.list_signals`` filtered by the ICP pre-filter, then call
    # ``score_workspace_candidates`` in CPU-budgeted batches (doc 14 §7.1-§7.2, §10.3).
    """
