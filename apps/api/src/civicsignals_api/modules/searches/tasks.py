"""Celery tasks for the searches module (doc 06 §8).

The saved-search **digest scheduler** (H3) does not live here. Notifications owns
delivery and the per-(saved-search, user) digest subscription table, so the beat
dispatcher + per-subscription delivery are
``notifications.dispatch_digests`` / ``notifications.send_digest`` (see
``modules/notifications/tasks.py``). It reads the saved search's stored ``filters``
back through this module's public ``searches.services`` surface
(``get_saved_search``) — the sanctioned cross-module seam (doc 06 §3).

This module currently registers no beat tasks of its own; importing
``celery_app`` keeps it discoverable by ``autodiscover_tasks`` for future work.
"""

from __future__ import annotations

from civicsignals_api.celery_app import celery_app  # noqa: F401
