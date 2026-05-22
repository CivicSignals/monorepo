"""Celery tasks for the searches module (doc 06 §8)."""

from __future__ import annotations

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="searches.dispatch_digests")
def dispatch_digests() -> None:
    """Hourly tick: send digests due in the next hour (TODO H3).

    # TODO H3: enumerate saved searches with an attached digest schedule (via
    #   ``searches.services``), re-run each one's stored ``filters`` against the
    #   owning workspace's feed (``signals.list_workspace_signals``), and enqueue a
    #   notification when there are new matches. The H1 saved-search row is the
    #   anchor this scheduler hangs off — deleting a search must cancel its digest
    #   (see the ``delete_saved_search`` seam).
    """
