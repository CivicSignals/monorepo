"""Celery application + beat schedule (doc 06 §8, doc 18 §6.1-6.2).

The same image runs different worker process types by binding to different
queues. Six container commands share this codebase: ``api``, ``worker_ingest``,
``worker_extract``, ``worker_score``, ``worker_notify``, ``scheduler``.

    celery -A civicsignals_api.celery_app worker -Q ingest   # worker_ingest
    celery -A civicsignals_api.celery_app worker -Q extract  # worker_extract
    celery -A civicsignals_api.celery_app worker -Q score    # worker_score
    celery -A civicsignals_api.celery_app worker -Q notify   # worker_notify
    celery -A civicsignals_api.celery_app beat                # scheduler (singleton)
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from civicsignals_api.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "civicsignals",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
)

# One dedicated queue per worker process type (doc 18 §6.2).
celery_app.conf.task_routes = {
    "ingestion.*": {"queue": "ingest"},
    "extraction.*": {"queue": "extract"},
    "signals.*": {"queue": "score"},
    "notifications.*": {"queue": "notify"},
    "searches.*": {"queue": "notify"},
    "integrations.*": {"queue": "notify"},
}

# Beat schedule (doc 06 §8). Tasks are defined in each module's tasks.py.
celery_app.conf.beat_schedule = {
    "extraction.run_pending_documents": {
        "task": "extraction.run_pending_documents",
        "schedule": 60.0,
    },
    "signals.dedupe_recent": {
        "task": "signals.dedupe_recent",
        "schedule": 300.0,
    },
    "searches.dispatch_digests": {
        "task": "searches.dispatch_digests",
        "schedule": crontab(minute=0),
    },
    "integrations.retry_failed_pushes": {
        "task": "integrations.retry_failed_pushes",
        "schedule": 600.0,
    },
    "contacts.revalidate_stale": {
        "task": "contacts.revalidate_stale",
        "schedule": crontab(hour=4, minute=0),
    },
}

# Import module tasks so Celery registers them (filled in by later epics).
celery_app.autodiscover_tasks(
    packages=["civicsignals_api.modules"],
    related_name="tasks",
)
