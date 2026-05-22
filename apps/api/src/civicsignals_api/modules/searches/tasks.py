"""Celery tasks for the searches module (doc 06 §8)."""
from __future__ import annotations

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="searches.dispatch_digests")
def dispatch_digests() -> None:
    """Hourly tick: send digests due in the next hour (TODO H3)."""
