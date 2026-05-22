"""Celery tasks for the ingestion module (doc 06 §8, doc 18 §6).

All tasks here are named ``ingestion.*`` and therefore route to the ``ingest``
queue (``celery_app.conf.task_routes``), drained by the ``worker_ingest`` process
(doc 18 §6.2). The headless-browser fetch path runs *only* on that worker — it
is the only image that carries the ``ingestion`` extra (Playwright) and the
Chromium binary (the lean ``api`` image carries neither).
"""

from __future__ import annotations

from collections.abc import Sequence

from civicsignals_api.celery_app import celery_app


@celery_app.task(name="ingestion.crawl_recipe")
def crawl_recipe(recipe_id: str) -> None:
    """Run one recipe's discover -> fetch cycle (TODO D4). Per-recipe cadence.

    The recipe DSL + runner exist (TODO D1, recipes module). Wiring this task to
    select a connector fetcher, persist raw docs to S3, and call
    ``ingestion.services.crawl_recipe`` is TODO D4/D6.
    """


@celery_app.task(name="ingestion.browser_fetch")
def browser_fetch(recipe_id: str, seed_urls: list[str]) -> int:
    """Crawl ``recipe_id`` over ``seed_urls`` via the headless-browser fetcher.

    The entry point for JS-heavy recipes (``connector: http_browser``, doc 18 §5
    wave 4) whose pages only have content after JavaScript runs. Routed to the
    ``ingest`` queue (the ``ingestion.*`` prefix) so it lands on ``worker_ingest``
    — the process whose image installs Playwright + Chromium (see the Dockerfile
    ``worker`` stage). Browser renders are heavier than static fetches; the queue
    is the buffer for bursts, and ``worker_ingest`` autoscales on queue depth via
    O3's Helm HPA (``workerIngest.hpa`` in
    ``infra/helm/civicsignals/templates/hpa.yaml``).

    Returns the number of canonical records produced (a placeholder until D3
    wires raw-document persistence + D4 the full crawl-run bookkeeping). The
    browser fetcher's pool is torn down before returning so a Celery worker that
    prefetches a different task type doesn't strand a browser process.
    """
    # Lazy import: keeps Playwright off the import path of any process that
    # doesn't render pages, and surfaces BrowserUnavailableError clearly if the
    # 'ingestion' extra is missing.
    from .browser import BrowserFetcher
    from .services import crawl_recipe as crawl

    seeds: Sequence[str] = list(seed_urls)
    with BrowserFetcher() as fetcher:
        records = crawl(recipe_id, fetcher, seeds)
    return len(records)
