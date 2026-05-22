"""Celery tasks for the integrations module (doc 06 §8)."""

from __future__ import annotations

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="integrations.retry_failed_pushes")
def retry_failed_pushes() -> None:
    """Retry failed CRM/webhook pushes (TODO K4, K5)."""
