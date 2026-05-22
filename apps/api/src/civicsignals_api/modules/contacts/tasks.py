"""Celery tasks for the contacts module (doc 06 §8)."""
from __future__ import annotations

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="contacts.revalidate_stale")
def revalidate_stale() -> None:
    """Daily revalidation of stale contact records (TODO C6)."""
