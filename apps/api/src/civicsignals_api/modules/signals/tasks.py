"""Celery tasks for the signals module (doc 06 §8)."""

from __future__ import annotations

import asyncio
import uuid

import structlog

from civicsignals_api.celery_app import celery_app
from civicsignals_api.db import SessionLocal
from civicsignals_api.modules.ingestion.locks import RedisLock, get_redis_client

# Per-workspace backfill lock namespace + TTL (F6, doc 14 §7.2 concurrency cap).
# The lock ensures only one backfill runs per workspace at a time; a second ICP
# change while one runs discards the stale run (the lock expires) and begins fresh.
_BACKFILL_LOCK_PREFIX = "civicsignals:signals:backfill:"
# A single batch takes well under a second (a few hundred DB rows); 10 minutes is
# generous enough to survive a slow worker without wedging the workspace forever.
_BACKFILL_LOCK_TTL_SECONDS = 600.0

log = structlog.get_logger(__name__)


@celery_app.task(name="signals.dedupe_recent")
def dedupe_recent() -> None:
    """Sweep recent signals for exact-key duplicates (doc 19 §7).

    Inline dedupe at store time (``signals.services.store_signal``, E5) already
    collapses duplicates as they arrive; this beat task is the backstop sweep that
    re-checks the recent window for duplicates that slipped past the inline path
    (e.g. a race between two workers storing the same key concurrently).

    # TODO E5+: implement the periodic sweep — group recent ``signals_signal`` rows
    # by ``(entity_id, signal_type, content_hash)`` within each type window and merge
    # any siblings via ``signals.dedupe.merge_signal``.
    """


@celery_app.task(name="signals.score_signal")
def score_signal(signal_id: str) -> int:
    """Fan a newly-created signal out to every matching workspace (F3, doc 14 §6).

    The production scoring trigger for the live matcher path (doc 14 §4.1). The
    extraction store step (E1/E4) promotes candidates into ``signals_signal`` rows in
    the ``worker_extract`` process; that process does **not** run the FastAPI app
    factory, so the in-process ``signal.created`` event bus has no subscriber there.
    Instead of publishing the event, the extraction task enqueues *this* task per
    newly-promoted signal so the fan-out runs in ``worker_score`` (the ``signals.*``
    prefix routes here, ``celery_app`` task routing), where scoring belongs (doc 18
    §6.2 per-queue separation). This is the **single** scoring trigger for new
    signals — we deliberately do not also publish ``signal.created`` from the worker,
    so a signal is scored exactly once per creation (no double-scoring).

    Idempotent: ``score_signal_for_all_workspaces`` sparse-upserts each
    ``signals_workspace_score`` row (ON CONFLICT, doc 14 §6.2/§7.3), so a Celery
    re-delivery re-scores to the same rows. Returns the number of score rows written.
    """
    return asyncio.run(_score_signal_async(signal_id))


async def _score_signal_async(signal_id_str: str) -> int:
    """Async body of :func:`score_signal` (testable without a Celery broker)."""
    from . import services

    try:
        sig_id = uuid.UUID(signal_id_str)
    except (ValueError, AttributeError):
        log.warning("signals.score_signal.bad_signal_id", signal_id=signal_id_str)
        return 0

    async with SessionLocal() as session:
        written = await services.score_signal_for_all_workspaces(session, signal_id=sig_id)
        await session.commit()
    log.info("signals.score_signal.done", signal_id=signal_id_str, written=written)
    return written


@celery_app.task(name="signals.rescore_workspace")
def rescore_workspace(workspace_id: str) -> int:
    """Re-score a workspace's historical signal window after an ICP change (F6).

    Implements the bounded async backfill (doc 14 §7.1-§7.2, §10.3):

    1. **Per-workspace lock** (Redis ``SET NX``): only one backfill runs per
       workspace at a time (the F6 concurrency cap).  A second ICP change while one
       is running will *coalesce* cleanly: the listener's sync pass already scored
       the freshest 1,000 candidates before enqueuing this task; when this task finds
       the lock held, it exits and the next-enqueued task (after the current one
       finishes and releases the lock) will pick up the remainder.  The TTL
       (:data:`_BACKFILL_LOCK_TTL_SECONDS`) is the crash-safety net.

    2. **ICP pre-filter + batch scoring**: loads the workspace's active ICP, pages
       through the historical signal window (``BACKFILL_LOOKBACK_DAYS``) in
       :data:`~.services.BACKFILL_BATCH_SIZE`-sized batches starting after the first
       :data:`~.services.BACKFILL_SYNC_LIMIT` candidates (already scored
       synchronously by the listener), and sparse-upserts a ``signals_workspace_score``
       row for each match (idempotent ON CONFLICT, doc 14 §7.3).

    Returns the total number of score rows written (across all async batches).
    """
    return asyncio.run(_rescore_workspace_async(workspace_id))


async def _rescore_workspace_async(workspace_id_str: str) -> int:
    """Async implementation of the F6 workspace backfill (see :func:`rescore_workspace`)."""
    from . import services

    try:
        ws_id = uuid.UUID(workspace_id_str)
    except (ValueError, AttributeError):
        log.warning("signals.rescore_workspace.bad_workspace_id", workspace_id=workspace_id_str)
        return 0

    lock_key = f"{_BACKFILL_LOCK_PREFIX}{workspace_id_str}"
    redis_client = get_redis_client()
    lock = RedisLock(redis_client, lock_key, ttl_seconds=_BACKFILL_LOCK_TTL_SECONDS)

    if not lock.acquire():
        log.info(
            "signals.rescore_workspace.skip_locked",
            workspace_id=workspace_id_str,
        )
        return 0

    total_written = 0
    try:
        # Load the active ICP once so repeated batch queries don't re-fetch it.
        async with SessionLocal() as session:
            from civicsignals_api.modules.icp import services as icp_services

            icp = await icp_services.get_active_icp(session, workspace_id=ws_id)

        if icp is None:
            log.info(
                "signals.rescore_workspace.no_active_icp",
                workspace_id=workspace_id_str,
            )
            return 0

        offset = services.BACKFILL_SYNC_LIMIT  # skip the sync batch the listener already scored
        batch_size = services.BACKFILL_BATCH_SIZE

        while True:
            async with SessionLocal() as session:
                signal_ids = await services.candidate_signal_ids_for_icp(
                    session, icp, offset=offset, limit=batch_size
                )
                if not signal_ids:
                    break
                written = await services.score_workspace_candidates(
                    session,
                    workspace_id=ws_id,
                    signal_ids=signal_ids,
                    icp=icp,
                )
                await session.commit()

            total_written += written
            offset += len(signal_ids)
            log.debug(
                "signals.rescore_workspace.batch",
                workspace_id=workspace_id_str,
                offset=offset,
                batch=len(signal_ids),
                written=written,
            )

        log.info(
            "signals.rescore_workspace.done",
            workspace_id=workspace_id_str,
            total_written=total_written,
        )
    finally:
        lock.release()

    return total_written
