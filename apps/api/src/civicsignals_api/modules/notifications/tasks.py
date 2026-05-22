"""Celery task definitions for the notifications module (doc 06 §8).

Bound to the ``notify`` queue via ``celery_app.conf.task_routes`` (``notifications.*``
→ notify) and discovered by ``autodiscover_tasks``.

H3 — saved-search digest scheduler
-----------------------------------
Two tasks implement the per-saved-search daily/weekly digest in the recipient's
local time:

* :func:`dispatch_digests` — the hourly beat task. Runs on the singleton
  ``scheduler`` (beat) and is guarded by a Redis **leader lock** (the same
  ``RedisLock`` the D4/F6 work uses) so that even if several beat processes fire,
  only one actually does the sweep. It loads the active (non-``off``) subscriptions,
  applies the pure :func:`~notifications.digest.is_due` predicate for *this* tick's
  ``now``, and enqueues one :func:`send_digest` per due subscription.

* :func:`send_digest` — per-subscription delivery on the ``notify`` worker. It first
  **claims the period** via :func:`services.mark_sent` (a guarded conditional UPDATE):
  if a concurrent worker already claimed it, this run is a no-op. On winning the
  claim it builds the digest payload (new signals since last send), then (H4) renders
  it into a branded HTML+text email (:mod:`notifications.templates`) and sends it via
  the B1 mailer (Mailpit in dev). The empty-digest case still sends a short note.

Distributed-safe dedupe: the leader lock prevents a duplicated *sweep*; the
``mark_sent`` period claim prevents a duplicated *send* (two sweeps, a beat retry,
or a re-delivered task). The dedupe key is the recipient's local day/ISO-week, so
double-fires within a period collapse to one send (mirrors the M5 FOIA guard).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import structlog

from civicsignals_api.celery_app import celery_app
from civicsignals_api.config import get_settings
from civicsignals_api.db import SessionLocal
from civicsignals_api.modules.ingestion.locks import RedisLock, get_redis_client

from . import services, templates

logger = structlog.get_logger(__name__)

# Leader lock so only one beat process runs the sweep per tick (doc 18 §6.1). The
# beat tick is hourly; a 10-minute lease is far longer than a sweep takes and short
# enough that a crashed leader is replaced well before the next tick.
_DISPATCH_LOCK_KEY = "civicsignals:notifications:digest-dispatch"
_DISPATCH_LOCK_TTL_SECONDS = 600.0


async def _dispatch_digests_async(now: datetime) -> int:
    """Async core of the dispatch sweep — returns the count enqueued."""
    async with SessionLocal() as session:
        active = await services.list_active_subscriptions(session)

    due = services.select_due_subscriptions(active, now=now)
    for sub in due:
        send_digest.delay(str(sub.id), now.isoformat())

    logger.info(
        "notifications.dispatch_digests.swept",
        active=len(active),
        due=len(due),
    )
    return len(due)


@celery_app.task(name="notifications.dispatch_digests")
def dispatch_digests() -> int:
    """Hourly beat: enqueue digests due in the recipients' local time (H3).

    Leader-locked (Redis ``SET NX``) so a duplicated beat fire does not double-sweep.
    Returns the number of :func:`send_digest` tasks enqueued this tick.
    """
    lock = RedisLock(get_redis_client(), _DISPATCH_LOCK_KEY, ttl_seconds=_DISPATCH_LOCK_TTL_SECONDS)
    if not lock.acquire():
        logger.info("notifications.dispatch_digests.skip_locked")
        return 0
    try:
        return asyncio.run(_dispatch_digests_async(datetime.now(UTC)))
    finally:
        lock.release()


async def _send_digest_async(
    subscription_id: uuid.UUID,
    now: datetime,
    *,
    sender: services.EmailSender | None = None,
) -> bool:
    """Async core of per-subscription delivery — True iff this run sent.

    ``sender`` is injectable so tests can substitute a recording transport instead
    of SMTP (the B1 mailer pattern); production passes ``None`` and the default SMTP
    transport (Mailpit in dev) is used.
    """
    async with SessionLocal() as session:
        sub = await services.get_digest_subscription_by_id(session, subscription_id=subscription_id)
        if sub is None:
            return False
        # The "new signals since" lower bound is the *prior* send time — capture it
        # before the claim updates ``last_sent_at`` to ``now``.
        since = sub.last_sent_at

        # Claim the period first (distributed-safe dedupe): a concurrent worker that
        # already claimed it loses the guarded UPDATE and we no-op.
        won = await services.mark_sent(session, subscription_id=subscription_id, now=now)
        await session.commit()
        if not won:
            logger.info(
                "notifications.send_digest.skip_already_sent",
                subscription_id=str(subscription_id),
            )
            return False

        payload = await services.build_digest_payload(session, subscription=sub, since=since)

    # H4: render the payload into the branded HTML+text digest and send it. The
    # period is already claimed above, so this won't double-send. A missing recipient
    # address (deleted user) means there's nothing to deliver.
    recipient_email = payload.get("recipient_email")
    signal_count = len(payload.get("signals", []))
    if not recipient_email:
        logger.warning(
            "notifications.send_digest.no_recipient",
            subscription_id=str(subscription_id),
        )
        return False

    settings = get_settings()
    message = templates.render_digest_email(
        payload,
        web_base_url=settings.web_base_url,
        to=str(recipient_email),
    )
    services.send_email(message, sender=sender)

    logger.info(
        "notifications.send_digest.sent",
        subscription_id=str(subscription_id),
        saved_search_id=payload.get("saved_search_id"),
        signal_count=signal_count,
    )
    return True


@celery_app.task(name="notifications.send_digest")
def send_digest(subscription_id: str, now_iso: str) -> bool:
    """Per-subscription delivery (H3/H4). Claims the period, renders, and sends.

    Idempotent: the :func:`services.mark_sent` period claim suppresses a double
    send if the task is re-delivered or two sweeps raced. ``now_iso`` is the sweep's
    tick time so the claimed period matches what the sweep judged due.
    """
    try:
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        now = datetime.now(UTC)
    return asyncio.run(_send_digest_async(uuid.UUID(subscription_id), now))
