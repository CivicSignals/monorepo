"""Celery tasks for the extraction module (doc 06 §8)."""
from __future__ import annotations

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="extraction.run_pending_documents")
def run_pending_documents() -> None:
    """Drain the pending-documents queue through the extraction chain (TODO E1)."""
