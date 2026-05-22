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
from civicsignals_api.telemetry import register_tasks as _register_telemetry_tasks

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
    # M5: FOIA reminder emails are notification-flavoured work — notify worker.
    "foia.*": {"queue": "notify"},
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
    # Anonymous self-host telemetry ping (O6).  No-op when
    # CIVICSIGNALS_TELEMETRY_ENABLED is false (the default).
    "telemetry.ping": {
        "task": "telemetry.ping",
        "schedule": crontab(hour=3, minute=0, day_of_week=1),  # weekly, Monday 03:00
    },
    # M5: FOIA reminder nudges — run every 6 hours so time zones don't cause
    # a 24-hour delay; the mark_reminded idempotency guard prevents double-send
    # within the same calendar day.
    "foia.send_foia_reminders": {
        "task": "foia.send_foia_reminders",
        "schedule": crontab(minute=0, hour="*/6"),
    },
}

# Import module tasks so Celery registers them (filled in by later epics).
celery_app.autodiscover_tasks(
    packages=["civicsignals_api.modules"],
    related_name="tasks",
)

# Register the telemetry ping task (O6).  Done here, after autodiscover, so
# the celery_app object is fully configured before the task is attached.
_register_telemetry_tasks(celery_app)
