# SPDX-License-Identifier: AGPL-3.0-only
"""In-process event-bus listeners for the signals module (F3 + F6).

**F3 — ``signal.created``**: When a signal lands and ``signal.created`` is
published (doc 14 §4.1: the matcher → scorer step of the lifecycle), the
:func:`_on_signal_created` listener runs the cheap pre-filter to find the
workspaces whose active ICP the signal *could* match, full-scores it against each,
and sparse-upserts a ``signals_workspace_score`` row for every match (doc 14 §6).
This is the live, real-time half of "global signal → per-workspace ranked feed";
the F6 backfill (doc 14 §7) is the symmetric historical half.

**F6 — ``icp.changed``**: When a workspace's ICP is created, updated, or activated,
the :func:`_on_icp_changed` listener runs a **synchronous** score of the first
:data:`~.services.BACKFILL_SYNC_LIMIT` candidates (so the feed has immediate results
after the change) and then enqueues the ``signals.rescore_workspace`` Celery task to
score the remainder in bounded batches on the score worker (doc 14 §7.1-§7.2).

**Isolation:** each listener opens its own short-lived async session and commits
independently, so a scoring failure never rolls back the domain transaction that
emitted the event (mirroring the admin audit listener). Failures are logged at
WARNING (not re-raised) — a transient failure does not fail the upstream request;
the async Celery task (for F6) or the nightly reconciliation sweep (doc 14 §10.1)
is the backstop for any signal a one-off failure skipped.

**Registration:** :func:`register_listeners` is called once at app startup (from
:mod:`civicsignals_api.main`), alongside the admin audit listeners.
"""

from __future__ import annotations

import uuid

import structlog

from civicsignals_api import events
from civicsignals_api.db import SessionLocal

from . import services

logger = structlog.get_logger(__name__)


async def _on_signal_created(payload: dict[str, object]) -> None:
    """Score a freshly-created signal for every workspace whose ICP matches (doc 14 §6).

    Reads ``signal_id`` from the event payload, opens a fresh committed session, and
    fans the signal out via :func:`signals.services.score_signal_for_all_workspaces`.
    A missing/malformed ``signal_id`` or any error is logged and swallowed.
    """
    raw = payload.get("signal_id")
    if raw is None:
        return
    try:
        signal_id = uuid.UUID(str(raw))
    except (ValueError, AttributeError):
        logger.warning("signals.score.bad_signal_id", signal_id=str(raw))
        return

    try:
        async with SessionLocal() as session:
            written = await services.score_signal_for_all_workspaces(session, signal_id=signal_id)
            await session.commit()
        logger.info("signals.score.signal_created", signal_id=str(signal_id), written=written)
    except Exception:
        logger.warning("signals.score.failed", signal_id=str(signal_id))


async def _on_icp_changed(payload: dict[str, object]) -> None:
    """Rescore a workspace's candidates when its ICP changes (F6, doc 14 §7).

    Two-phase response to an ``icp.changed`` event:

    1. **Synchronous phase** — score the first :data:`~.services.BACKFILL_SYNC_LIMIT`
       candidates against the new ICP in the current async context so the workspace
       feed reflects the change immediately (doc 14 §7.2).

    2. **Async phase** — enqueue ``signals.rescore_workspace`` on the score queue to
       score the remainder in bounded batches (doc 14 §7.1-§7.2). The Celery task
       holds a per-workspace Redis lock so concurrent backfills coalesce rather than
       pile up (doc 14 §7.2 concurrency cap).

    A missing/malformed ``workspace_id`` or any error in the sync phase is logged
    and swallowed — the async task provides the backstop.
    """
    raw = payload.get("workspace_id")
    if raw is None:
        return
    try:
        workspace_id = uuid.UUID(str(raw))
    except (ValueError, AttributeError):
        logger.warning("signals.backfill.bad_workspace_id", workspace_id=str(raw))
        return

    workspace_id_str = str(workspace_id)

    # --- Synchronous phase: score the first BACKFILL_SYNC_LIMIT candidates ----
    sync_written = 0
    try:
        async with SessionLocal() as session:
            from civicsignals_api.modules.icp import services as icp_services

            icp = await icp_services.get_active_icp(session, workspace_id=workspace_id)
            if icp is not None:
                signal_ids = await services.candidate_signal_ids_for_icp(
                    session, icp, offset=0, limit=services.BACKFILL_SYNC_LIMIT
                )
                if signal_ids:
                    sync_written = await services.score_workspace_candidates(
                        session,
                        workspace_id=workspace_id,
                        signal_ids=signal_ids,
                        icp=icp,
                    )
                    await session.commit()
        logger.info(
            "signals.backfill.sync_done",
            workspace_id=workspace_id_str,
            written=sync_written,
        )
    except Exception:
        logger.warning("signals.backfill.sync_failed", workspace_id=workspace_id_str)

    # --- Async phase: enqueue the remainder on the score queue ----------------
    try:
        from .tasks import rescore_workspace

        rescore_workspace.delay(workspace_id_str)
        logger.info(
            "signals.backfill.task_enqueued",
            workspace_id=workspace_id_str,
        )
    except Exception:
        logger.warning("signals.backfill.enqueue_failed", workspace_id=workspace_id_str)


def register_listeners() -> None:
    """Subscribe the F3 + F6 scoring listeners to the in-process event bus.

    Idempotent in the sense :func:`~civicsignals_api.events.subscribe` is append-only;
    call :func:`unregister_listeners` first if a test needs isolation.
    """
    events.subscribe(events.SIGNAL_CREATED, _on_signal_created)
    events.subscribe(events.ICP_CHANGED, _on_icp_changed)
    logger.info("signals_scoring_listener_registered")


def unregister_listeners() -> None:
    """Remove the F3 + F6 scoring listeners (test teardown helper)."""
    events.unsubscribe(events.SIGNAL_CREATED, _on_signal_created)
    events.unsubscribe(events.ICP_CHANGED, _on_icp_changed)
