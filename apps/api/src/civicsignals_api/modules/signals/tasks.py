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
    """Re-score a workspace after an ICP change (TODO F3, F6)."""
