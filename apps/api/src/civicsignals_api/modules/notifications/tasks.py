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

* :func:`send_digest` — per-subscription delivery on the ``notify`` worker. For
  **at-least-once delivery** it does the cheap read-only dedupe check
  (:func:`services.already_sent_this_period`), builds the digest payload (new
  signals since last send), (H4) renders it into a branded HTML+text email
  (:mod:`notifications.templates`), **sends it via the B1 mailer (Mailpit in dev)**,
  and only **claims the period** via :func:`services.mark_sent` (a guarded conditional
  UPDATE) *after a confirmed send*. If the relay fails the period stays unclaimed and
  the next sweep retries — the prior "claim then send" order could silently lose a
  period's digest when ``send_email`` swallowed an SMTP error. The empty-digest case
  still sends a short note.

Distributed-safe dedupe: the leader lock prevents a duplicated *sweep*; the
``mark_sent`` period claim (now post-send) collapses duplicates (two sweeps, a beat
retry, or a re-delivered task). The dedupe key is the recipient's local day/ISO-week.
At-least-once is safe here: the leader-locked sweep serializes the dispatch and the
per-period guarded UPDATE collapses any duplicate, so a send-then-claim window can
at worst re-send once (mirrors the M5 FOIA guard intent).
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
from .unsubscribe import make_unsubscribe_token

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

    TODO(NIT): a multi-hour scheduler outage spanning a recipient's weekly send-hour
    means that week is skipped rather than caught up on recovery (``is_due`` only
    fires within the send-hour window). Acceptable for MVP; revisit with a
    catch-up/grace window if weekly reliability becomes a concern.
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

        # Pre-claim dedupe check (read-only): if the current period is already the
        # stored ``last_sent_period`` it was sent — skip without re-sending. This is
        # the cheap guard for a re-delivered task / second sweep; the *authoritative*
        # claim is the guarded ``mark_sent`` UPDATE below, run only after a confirmed
        # send. The ``dispatch_digests`` Redis leader-lock serializes the sweep, so a
        # narrow window between this read and the claim is closed by the per-period
        # guarded UPDATE collapsing duplicates.
        if services.already_sent_this_period(sub, now=now):
            logger.info(
                "notifications.send_digest.skip_already_sent",
                subscription_id=str(subscription_id),
            )
            return False

        # The "new signals since" lower bound is the *prior* send time. We have not
        # claimed the period yet, so ``last_sent_at`` still holds the prior send.
        # TODO(NIT): this windows on signal ``occurred_at``/``published_at``, not on
        # ingestion time, so a signal ingested late (occurred before the last send)
        # can be missed. Acceptable for MVP; revisit when ingestion-time bounding lands.
        since = sub.last_sent_at

        payload = await services.build_digest_payload(session, subscription=sub, since=since)

        # H4: render the payload into the branded HTML+text digest. A missing
        # recipient address (deleted user) means there's nothing to deliver — and we
        # must NOT claim the period (nothing was sent).
        recipient_email = payload.get("recipient_email")
        signal_count = len(payload.get("signals", []))
        if not recipient_email:
            logger.warning(
                "notifications.send_digest.no_recipient",
                subscription_id=str(subscription_id),
            )
            return False

        # H5: mint a signed one-click unsubscribe token bound to *this* subscription
        # and thread both the human confirm link (footer + List-Unsubscribe) and the
        # API one-click POST URL (List-Unsubscribe-Post, RFC 8058) into the email.
        settings = get_settings()
        token = make_unsubscribe_token(subscription_id, settings=settings)
        message = templates.render_digest_email(
            payload,
            web_base_url=settings.web_base_url,
            to=str(recipient_email),
            unsubscribe_url=templates.unsubscribe_landing_url(settings.web_base_url, token),
            unsubscribe_post_url=templates.unsubscribe_post_url(
                settings.api_base_url, settings.api_v1_prefix, token
            ),
        )

        # At-least-once delivery: send FIRST, claim the period only AFTER a confirmed
        # send. If the relay is down, ``send_email`` returns ``False`` and we leave the
        # period unclaimed so the next sweep retries — rather than committing the claim
        # and silently losing this period's digest.
        if not services.send_email(message, sender=sender):
            logger.warning(
                "notifications.send_digest.send_failed",
                subscription_id=str(subscription_id),
                saved_search_id=payload.get("saved_search_id"),
            )
            return False

        # Send confirmed — claim the period (guarded UPDATE) and commit. The per-period
        # dedupe key collapses any concurrent duplicate, so a rare double-send (two
        # sweeps racing past the read-only check above) is bounded to at most one extra.
        won = await services.mark_sent(session, subscription_id=subscription_id, now=now)
        await session.commit()
        if not won:
            # Another worker claimed the period between our send and our claim; the
            # send already happened, so this is a harmless at-least-once duplicate.
            logger.info(
                "notifications.send_digest.claim_lost_after_send",
                subscription_id=str(subscription_id),
            )

    logger.info(
        "notifications.send_digest.sent",
        subscription_id=str(subscription_id),
        saved_search_id=payload.get("saved_search_id"),
        signal_count=signal_count,
    )
    return True


@celery_app.task(name="notifications.send_digest")
def send_digest(subscription_id: str, now_iso: str) -> bool:
    """Per-subscription delivery (H3/H4). Renders, sends, then claims the period.

    At-least-once: the period is claimed (:func:`services.mark_sent`) only after a
    confirmed send, so a relay failure leaves it unclaimed for the next sweep to
    retry. Idempotent: the per-period dedupe (read-only pre-check + guarded UPDATE)
    suppresses a double send if the task is re-delivered or two sweeps raced.
    ``now_iso`` is the sweep's tick time so the claimed period matches what the sweep
    judged due.
    """
    try:
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        now = datetime.now(UTC)
    return asyncio.run(_send_digest_async(uuid.UUID(subscription_id), now))
