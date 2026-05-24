"""Celery tasks for the integrations module (doc 06 §8, K1 + L3).

``retry_failed_pushes`` is the beat-scheduled sweeper (``celery_app`` registers
it at 600s): it finds push-log rows in ``failed`` status whose ``retry_at`` is
due and re-runs each through :func:`services.execute_push`, which applies the
exponential backoff and dead-letters once attempts are exhausted (K5). A
re-pushed row reuses its connection's auto-refreshed token.

``retry_failed_webhook_deliveries`` (L3) is the analogous sweeper for webhook
delivery rows. It follows the same pattern: find due failed rows, re-POST with
the stored request body (same event_id for idempotency), dead-letter on
exhaustion.
"""

from __future__ import annotations

import asyncio

import structlog

from civicsignals_api.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="integrations.retry_failed_pushes")
def retry_failed_pushes() -> int:
    """Retry due failed pushes with backoff; dead-letter on exhaustion (K5).

    Returns the number of pushes re-attempted (for observability/tests). Runs the
    async sweeper in its own event loop (the Celery worker is sync), opening its
    own ``SessionLocal``.
    """
    return asyncio.run(_retry_failed_pushes_async())


async def _retry_failed_pushes_async() -> int:
    from civicsignals_api.config import get_settings
    from civicsignals_api.db import SessionLocal

    from . import services
    from .providers import PushRequest

    settings = get_settings()
    attempted = 0
    async with SessionLocal() as session:
        due = await services.due_failed_pushes(session)
        # One shared HTTP client across the batch (closed at the end).
        http = services.default_http_client()
        try:
            for log in due:
                connection = await services.get_connection_unscoped(session, log.connection_id)
                if connection is None:  # pragma: no cover - cascade should prevent
                    continue
                request = PushRequest(
                    target=log.target,
                    payload=dict(log.request),
                    idempotency_key=log.idempotency_key,
                    external_id=log.external_id,
                )
                await services.execute_push(
                    session,
                    connection=connection,
                    log=log,
                    request=request,
                    settings=settings,
                    http_client=http,
                )
                attempted += 1
            await session.commit()
        finally:
            await http.aclose()

    logger.info("integrations_retry_failed_pushes", attempted=attempted)
    return attempted


# ---------------------------------------------------------------------------
# L3: Webhook delivery retry sweeper
# ---------------------------------------------------------------------------


@celery_app.task(name="integrations.retry_failed_webhook_deliveries")
def retry_failed_webhook_deliveries() -> int:
    """Retry due failed webhook deliveries with backoff; dead-letter on exhaustion (L3).

    Returns the number of deliveries re-attempted. Runs the async sweeper in its
    own event loop (the Celery worker is sync), opening its own ``SessionLocal``.
    """
    return asyncio.run(_retry_failed_webhook_deliveries_async())


async def _retry_failed_webhook_deliveries_async() -> int:
    from civicsignals_api.config import get_settings
    from civicsignals_api.db import SessionLocal

    from . import services

    settings = get_settings()
    attempted = 0
    async with SessionLocal() as session:
        due = await services.due_failed_webhook_deliveries(session)
        # Release the read transaction before the network-bound retry loop. Under
        # PgBouncer transaction-mode pooling (doc 06 §4) an open transaction pins a
        # pooled server connection, so holding one across every delivery's HTTP call
        # keeps the connection checked out for the whole batch — and batch after
        # batch that exhausts the pool, hanging every DB-backed request. Commit now
        # (the read has nothing to lose) and again after each delivery, so each runs
        # in its own short transaction that frees the connection before the next HTTP
        # round-trip. Per-delivery commits also stop one failure from rolling back
        # already-delivered rows.
        await session.commit()
        http = services.default_http_client()
        try:
            for delivery in due:
                sub = await services.get_webhook_subscription_unscoped(
                    session, delivery.subscription_id
                )
                if sub is None:  # pragma: no cover - cascade should prevent
                    continue
                await services.execute_webhook_delivery(
                    session,
                    subscription=sub,
                    delivery=delivery,
                    http_client=http,
                    settings=settings,
                )
                await session.commit()
                attempted += 1
        finally:
            await http.aclose()

    logger.info("integrations_retry_failed_webhook_deliveries", attempted=attempted)
    return attempted
