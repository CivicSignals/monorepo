"""Celery tasks for the integrations module (doc 06 §8, K1).

``retry_failed_pushes`` is the beat-scheduled sweeper (``celery_app`` registers
it at 600s): it finds push-log rows in ``failed`` status whose ``retry_at`` is
due and re-runs each through :func:`services.execute_push`, which applies the
exponential backoff and dead-letters once attempts are exhausted (K5). A
re-pushed row reuses its connection's auto-refreshed token.
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
