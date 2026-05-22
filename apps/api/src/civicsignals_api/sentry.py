"""Sentry error-tracking integration (doc 06 §9, A6).

Sentry is **config-gated**: it is a no-op when ``SENTRY_DSN`` is not set.  No
Sentry network calls are made in that case, so the app runs without any external
account.  Self-host options: Sentry OSS or GlitchTip.

PII scrubbing defaults (doc 06 §11):
- ``send_default_pii=False`` -- never attach cookies, session headers, or user IP.
- ``max_request_body_size="never"`` -- never forward request bodies (may contain
  customer ICP free-text or contact PII).
- The ``before_send`` hook strips the ``Authorization`` and ``X-Api-Key`` headers
  from captured events as a belt-and-suspenders guard.

TODO LC-15: finalise release tagging (``release=``) and upload source maps in the
release-images CI workflow so Sentry can de-minify stack frames.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_SCRUB_HEADERS = frozenset(
    {
        "authorization",
        "x-api-key",
        "cookie",
        "set-cookie",
        "x-csrf-token",
    }
)


def _scrub_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip sensitive request headers before the event reaches Sentry."""
    request = event.get("request", {})
    headers: dict[str, str] = request.get("headers", {}) or {}
    scrubbed = {k: v for k, v in headers.items() if k.lower() not in _SCRUB_HEADERS}
    if scrubbed != headers:
        request["headers"] = scrubbed
        event["request"] = request
    return event


def init_sentry(
    dsn: str | None,
    environment: str = "development",
    traces_sample_rate: float = 0.05,
    profiles_sample_rate: float = 0.0,
) -> None:
    """Initialise Sentry SDK if ``dsn`` is provided; otherwise a no-op.

    Args:
        dsn: Full Sentry DSN (e.g. ``https://<key>@<host>/<project>``).
             Pass ``None`` or empty string to disable.
        environment: Sentry environment tag (matches ``Settings.environment``).
        traces_sample_rate: Fraction of transactions to trace (0.0-1.0).
        profiles_sample_rate: Fraction of sampled transactions to profile (0.0-1.0).
    """
    if not dsn:
        logger.debug("sentry_dsn not set -- Sentry disabled")
        return

    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration
    from sentry_sdk.types import Event, Hint

    def before_send(event: Event, hint: Hint) -> Event | None:
        """Delegate to ``_scrub_event`` using Sentry's typed Event alias."""
        result = _scrub_event(event, hint)  # type: ignore[arg-type]
        return result  # type: ignore[return-value]

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        # Belt-and-suspenders PII guard: never send request bodies or default PII
        # (cookies, session headers, user IP) -- doc 06 §11.
        send_default_pii=False,
        max_request_body_size="never",
        before_send=before_send,
        # Performance monitoring.
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            CeleryIntegration(monitor_beat_tasks=False),
        ],
        # TODO LC-15: set release= to the image tag / git SHA from the CI env var.
    )
    logger.info("sentry_initialized", extra={"environment": environment})
