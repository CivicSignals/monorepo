"""Celery tasks for the ingestion module (doc 06 §8, doc 18 §6).

All tasks here are named ``ingestion.*`` and therefore route to the ``ingest``
queue (``celery_app.conf.task_routes``), drained by the ``worker_ingest`` process
(doc 18 §6.2). The headless-browser fetch path runs *only* on that worker — it
is the only image that carries the ``ingestion`` extra (Playwright) and the
Chromium binary (the lean ``api`` image carries neither).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import structlog

from civicsignals_api.celery_app import celery_app

log = structlog.get_logger(__name__)


@celery_app.task(name="ingestion.crawl_recipe")
def crawl_recipe(recipe_id: str, recipe_version: int | None = None) -> int:
    """Run one recipe's discover -> fetch -> extract -> normalize cycle (D4/D6).

    Dispatched by the scheduler's ``ingestion.dispatch_due_recipes`` beat task on
    each recipe's cadence, this is the per-recipe crawl entry point on the
    ``ingest`` queue (``ingestion.*`` prefix -> ``worker_ingest``). It takes a
    **per-recipe run lock** (doc 18 §6.4) so a recipe that crawls slower than its
    cadence — or a duplicate dispatch — isn't crawled concurrently / re-entered
    while a prior run is still in flight; if the lock is held the task is a no-op
    for this dispatch (the in-flight run will produce the records). ``recipe_version``
    is passed by the scheduler for provenance/logging (it pins the version the run
    was scheduled against, doc 18 §3.5); the runner re-reads the on-disk version.

    Connector selection + the full lifecycle live in
    ``services.crawl_recipe_with_connector`` (D6); seed URLs are connector-derived
    (most connectors read them from ``connector_config`` — doc 18 §2.1), so the
    scheduler doesn't pass any. Returns the number of canonical records produced
    (0 when the run lock was already held). Raw-document persistence + crawl-run
    bookkeeping layer on top in the storage seam (D3).
    """
    from .locks import get_redis_client, recipe_run_lock
    from .services import crawl_recipe_with_connector

    with recipe_run_lock(get_redis_client(), recipe_id) as acquired:
        if not acquired:
            # A prior run is still in flight; skip rather than crawl concurrently.
            log.info("ingestion.crawl_recipe.skip_locked", recipe_id=recipe_id)
            return 0
        records = crawl_recipe_with_connector(recipe_id, seed_urls=[])
        return len(records)


@celery_app.task(name="ingestion.dispatch_due_recipes")
def dispatch_due_recipes() -> int:
    """Scan active recipes and enqueue a crawl for each one that's due (D4).

    The scheduler's beat-driven dispatcher (doc 06 §8, doc 18 §3, §6). Wired into
    ``celery_app.conf.beat_schedule`` to tick every minute; runs only on the
    ``scheduler`` process. Each tick:

    1. **Leader election** — take the Redis leader lock so only one ``scheduler``
       container schedules even if several run (doc 18 §6.1). A non-leader tick is
       a no-op (returns 0).
    2. **Backpressure gate (TODO D15)** — pause enqueuing when the ``ingest`` queue
       depth exceeds the hard threshold; resume below the soft one (doc 18 §6.4).
       The hook is in :func:`_dispatch_due_recipes_async`.
    3. **Due scan** — for each active recipe, compare ``now`` against its
       ``ingestion_recipe_schedule`` run-state (cron + per-recipe jitter, doc 18
       §6.4) and enqueue ``ingestion.crawl_recipe`` on the ``ingest`` queue for the
       due ones, advancing their ``last_run_at`` / ``next_run_at``.

    Returns the number of recipes dispatched this tick (0 when not leader or none
    due). Idempotency at the *run* level is the per-recipe run lock in
    :func:`crawl_recipe`; this task only decides *whether to enqueue*.
    """
    from .locks import get_redis_client, leader_lock

    with leader_lock(get_redis_client()) as is_leader:
        if not is_leader:
            log.debug("ingestion.dispatch_due_recipes.not_leader")
            return 0
        return asyncio.run(_dispatch_due_recipes_async())


async def _dispatch_due_recipes_async() -> int:
    """Body of the dispatcher (extracted so it's testable without a real broker)."""
    from civicsignals_api.db import SessionLocal

    from . import scheduler as scheduler_module
    from . import services

    # --- Backpressure seam (TODO D15, doc 18 §6.4) --------------------------
    # When the ingest queue depth exceeds its hard threshold the scheduler must
    # stop enqueuing new recipe runs until the queue drains below the soft
    # threshold (in-flight runs are never killed). D15 builds the queue-depth
    # probe + thresholds on this hook; until then it is always "have headroom".
    if not _ingest_queue_has_headroom():
        log.warning("ingestion.dispatch_due_recipes.backpressure_paused")
        return 0

    now = scheduler_module.utcnow()
    recipes = scheduler_module.load_active_recipes()

    dispatched: list[tuple[str, int]] = []
    async with SessionLocal() as session:
        states = {s.recipe_id: s for s in await services.list_recipe_schedules(session)}
        for recipe in recipes:
            state = states.get(recipe.recipe_id)
            if state is None:
                # First sighting — seed the run-state row (due immediately).
                state = await services.get_or_create_recipe_schedule(
                    session,
                    recipe.recipe_id,
                    recipe_version=recipe.version,
                    cron=recipe.schedule.get("cron"),
                )
            if state.paused:
                continue  # paused recipe: skip, but its past signals stay visible.
            decision = scheduler_module.evaluate(
                recipe,
                last_run_at=state.last_run_at,
                next_run_at_stored=state.next_run_at,
                now=now,
            )
            if not decision.due:
                continue
            await services.mark_recipe_dispatched(
                session,
                recipe.recipe_id,
                last_run_at=now,
                next_run_at=decision.next_run_at,
                recipe_version=recipe.version,
                cron=decision.cadence.cron,
            )
            dispatched.append((recipe.recipe_id, recipe.version))
        await session.commit()

    # Enqueue *after* the state is committed so a crash between commit and enqueue
    # at worst delays a run by one cadence (next tick re-fires) rather than
    # double-firing — the per-recipe run lock also guards the duplicate case.
    for recipe_id, version in dispatched:
        crawl_recipe.delay(recipe_id, version)
    log.info("ingestion.dispatch_due_recipes.dispatched", count=len(dispatched))
    return len(dispatched)


def _ingest_queue_has_headroom() -> bool:
    """Backpressure gate for the dispatcher (D15; doc 18 §6.4).

    Returns True while the ``ingest`` queue has room to accept new recipe runs.
    Probes the Redis ``LLEN`` of the ``ingest`` list and applies hysteresis:

    * Pause when depth >= hard threshold (``ingest_queue_hard_threshold``).
    * Once paused, resume only when depth < soft threshold
      (``ingest_queue_soft_threshold``).

    State is held in the module-level :class:`~backpressure.BackpressureGate`
    singleton so hysteresis persists correctly across consecutive beat ticks.
    The probe and gate are injectable via
    :func:`~backpressure.check_ingest_queue_headroom`'s keyword arguments —
    tests pass a fake probe to drive any depth without a live Redis.
    """
    from .backpressure import check_ingest_queue_headroom

    return check_ingest_queue_headroom()


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
