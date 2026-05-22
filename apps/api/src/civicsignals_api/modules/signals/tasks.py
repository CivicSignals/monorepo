"""Celery tasks for the signals module (doc 06 §8)."""
from __future__ import annotations

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="signals.dedupe_recent")
def dedupe_recent() -> None:
    """Exact-match dedupe over the recent window (TODO E5)."""


@celery_app.task(name="signals.rescore_workspace")
def rescore_workspace(workspace_id: str) -> None:
    """Re-score a workspace after an ICP change (TODO F3, F6)."""
