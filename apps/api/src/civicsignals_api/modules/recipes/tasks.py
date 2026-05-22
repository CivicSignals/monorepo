"""Celery task definitions for the recipes module.

Bound to the module's queue via `celery_app.conf.task_routes` and discovered by
`autodiscover_tasks`.

E7 recipe drift detection: the recipes module owns the *logic* (rolling metrics,
the auto-pause decision, the GitHub auto-issue) in ``drift.py`` + ``services.py``,
but the **beat task that runs it lives in the ingestion module**
(``ingestion.evaluate_recipe_drift``). That is deliberate — applying the auto-pause
means flipping the authoritative scheduler pause flag in ingestion
(``ingestion_recipe_schedule.paused``, D4), and recipes must not import ingestion
(doc 06 §3). So the bridge runs from the ingestion side, calling into recipes'
``services.evaluate_recipe_drift``.

QA-7: the weekly extraction quality sampling beat task
(``recipes.sample_extraction_quality``) lives in ``quality_sampling.py`` and is
imported here so Celery's ``autodiscover_tasks`` picks it up.
"""

from __future__ import annotations

from civicsignals_api.celery_app import celery_app  # noqa: F401

# Import the QA-7 beat task so autodiscover_tasks registers it.
from .quality_sampling import sample_extraction_quality_task  # noqa: F401
