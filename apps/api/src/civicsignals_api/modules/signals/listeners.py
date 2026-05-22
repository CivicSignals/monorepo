# SPDX-License-Identifier: AGPL-3.0-only
"""In-process event-bus listener that scores a new signal for every workspace (F3).

When a signal lands and ``signal.created`` is published (doc 14 §4.1: the matcher →
scorer step of the lifecycle), this listener runs the cheap pre-filter to find the
workspaces whose active ICP the signal *could* match, full-scores it against each,
and sparse-upserts a ``signals_workspace_score`` row for every match (doc 14 §6).
This is the live, real-time half of "global signal → per-workspace ranked feed";
the F6 backfill (doc 14 §7) is the symmetric historical half.

**Isolation:** the listener opens its own short-lived async session and commits
independently, so a scoring failure never rolls back the domain transaction that
emitted the event (mirroring the admin audit listener). The publisher fires
``signal.created`` **after** committing the new signal, so this fresh session sees
it. Failures are logged at WARNING (not re-raised) so a transient scorer error never
fails the upstream pipeline — the nightly reconciliation sweep (doc 14 §10.1) is the
backstop for any signal a one-off failure skipped.

**Registration:** :func:`register_listeners` is called once at app startup (from
:mod:`civicsignals_api.main`), alongside the admin audit listeners.

# TODO F6: ICP-change backfill subscribes its own listener here (re-score a window
# for one workspace when its ICP changes, doc 14 §7) — the symmetric direction.
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


def register_listeners() -> None:
    """Subscribe the F3 scoring listener to the in-process event bus.

    Idempotent in the sense :func:`~civicsignals_api.events.subscribe` is append-only;
    call :func:`unregister_listeners` first if a test needs isolation.
    """
    events.subscribe(events.SIGNAL_CREATED, _on_signal_created)
    logger.info("signals_scoring_listener_registered")


def unregister_listeners() -> None:
    """Remove the F3 scoring listener (test teardown helper)."""
    events.unsubscribe(events.SIGNAL_CREATED, _on_signal_created)
