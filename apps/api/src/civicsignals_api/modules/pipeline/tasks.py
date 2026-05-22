"""Celery task definitions for the pipeline module.

Bound to the module's queue via `celery_app.conf.task_routes` and discovered by
`autodiscover_tasks`. Concrete task bodies land in later epics.
"""

from __future__ import annotations

from civicsignals_api.celery_app import celery_app  # noqa: F401
