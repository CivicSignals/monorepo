"""Anonymous self-host telemetry ping (O6).

OFF by default — enabled only when ``CIVICSIGNALS_TELEMETRY_ENABLED=true``.

What is sent
------------
A single JSON POST to ``CIVICSIGNALS_TELEMETRY_ENDPOINT`` once per week (via a
Celery beat task registered as ``"telemetry.ping"``).  The payload contains:

    {
        "instance_id":    "<random UUID, stable across restarts>",
        "version":        "0.1.0",
        "deploy_type":    "self-host",
        "workspace_count_bucket": "<0|1-5|6-25|26-100|101+>",
        "signal_count_bucket":    "<0|1k|10k|100k|1M|10M+>"
    }

What is **never** sent
----------------------
- Any email address, name, or contact record.
- Any signal content, ICP text, or scraped document.
- Any workspace ID, user ID, or other linkable identifier.
- Any IP address (the receiving endpoint sees only the egress NAT IP; we do not
  log or store it beyond the TCP connection).

Disable
-------
Leave ``CIVICSIGNALS_TELEMETRY_ENABLED`` unset (default) or set it to ``false``.
The Celery beat task becomes a no-op and no network connection is ever opened.

See ``docs/self-host/telemetry.md`` for the full transparency statement.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application version (single source of truth with the pyproject version).
# ---------------------------------------------------------------------------
_APP_VERSION = "0.1.0"
_DEPLOY_TYPE = "self-host"


# ---------------------------------------------------------------------------
# Instance-id — a random UUID persisted to a small local file.
# NOT derived from any user or workspace data.
# ---------------------------------------------------------------------------


def _load_or_create_instance_id(id_path: str) -> str:
    """Return a stable random instance-id, creating it if it doesn't exist.

    The id is stored at *id_path*.  If the file cannot be read or written (e.g.
    the path is read-only) a fresh random id is returned for this session only
    — the ping still works, but the id will change on the next restart.
    """
    path = Path(id_path)
    try:
        text = path.read_text().strip()
        # Validate that it is a UUID before trusting it.
        uuid.UUID(text)
        return text
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        logger.debug("telemetry: could not read instance-id from %s, regenerating", id_path)

    fresh_id = str(uuid.uuid4())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh_id)
    except OSError:
        logger.debug("telemetry: could not persist instance-id to %s", id_path)

    return fresh_id


# ---------------------------------------------------------------------------
# Coarse bucketing — replaces exact counts with a size band so the receiving
# endpoint learns nothing about individual tenant scale.
# ---------------------------------------------------------------------------

_WORKSPACE_BUCKETS = [
    (0, "0"),
    (1, "1-5"),
    (6, "6-25"),
    (26, "26-100"),
    (101, "101+"),
]

_SIGNAL_BUCKETS = [
    (0, "0"),
    (1, "1k"),
    (1_000, "10k"),
    (10_000, "100k"),
    (100_000, "1M"),
    (1_000_000, "10M+"),
]


def _bucket(value: int, thresholds: list[tuple[int, str]]) -> str:
    """Return the label of the highest threshold that *value* meets."""
    label = thresholds[0][1]
    for threshold, bucket_label in thresholds:
        if value >= threshold:
            label = bucket_label
        else:
            break
    return label


def workspace_bucket(count: int) -> str:
    """Coarse workspace-count band (e.g. "6-25")."""
    return _bucket(count, _WORKSPACE_BUCKETS)


def signal_bucket(count: int) -> str:
    """Coarse signal-count band (e.g. "100k")."""
    return _bucket(count, _SIGNAL_BUCKETS)


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

# The exhaustive set of keys allowed in the telemetry payload.  Any addition
# here must be reviewed for PII risk and documented in telemetry.md.
_ALLOWED_PAYLOAD_KEYS = frozenset(
    {
        "instance_id",
        "version",
        "deploy_type",
        "workspace_count_bucket",
        "signal_count_bucket",
    }
)


def build_payload(
    instance_id: str,
    workspace_count: int,
    signal_count: int,
) -> dict[str, str]:
    """Return the anonymous telemetry payload dict.

    Only keys listed in ``_ALLOWED_PAYLOAD_KEYS`` are present.  The caller can
    assert this invariant in tests (and we do).
    """
    payload: dict[str, str] = {
        "instance_id": instance_id,
        "version": _APP_VERSION,
        "deploy_type": _DEPLOY_TYPE,
        "workspace_count_bucket": workspace_bucket(workspace_count),
        "signal_count_bucket": signal_bucket(signal_count),
    }
    # Belt-and-suspenders: strip any key that somehow escaped the allowlist.
    return {k: v for k, v in payload.items() if k in _ALLOWED_PAYLOAD_KEYS}


# ---------------------------------------------------------------------------
# Aggregate count fetcher — queries the DB for coarse counts (no PII).
# ---------------------------------------------------------------------------


async def _fetch_counts() -> tuple[int, int]:
    """Return ``(workspace_count, signal_count)`` from the DB.

    Uses a fresh direct connection so it does not hold PgBouncer slots.
    Returns ``(0, 0)`` on any error — best-effort only.
    """
    try:
        from sqlalchemy import func, select, text

        from civicsignals_api.db import engine

        async with engine.connect() as conn:
            # Workspace count — table may not exist yet in a fresh install.
            try:
                ws_row = await conn.execute(
                    select(func.count()).select_from(text("accounts_workspace"))
                )
                workspace_count: int = ws_row.scalar_one() or 0
            except Exception:  # pragma: no cover
                workspace_count = 0

            # Signal count — same guard.
            try:
                sig_row = await conn.execute(
                    select(func.count()).select_from(text("signals_signal"))
                )
                signal_count: int = sig_row.scalar_one() or 0
            except Exception:  # pragma: no cover
                signal_count = 0

        return workspace_count, signal_count
    except Exception:  # pragma: no cover
        logger.debug("telemetry: could not fetch aggregate counts", exc_info=True)
        return 0, 0


# ---------------------------------------------------------------------------
# Ping sender
# ---------------------------------------------------------------------------


async def send_ping(
    *,
    endpoint: str,
    instance_id: str,
    workspace_count: int,
    signal_count: int,
    timeout: float = 10.0,  # noqa: ASYNC109
) -> None:
    """POST the anonymous payload to *endpoint*.

    All errors are swallowed — a telemetry failure must never surface to the
    operator or affect the application in any way.
    """
    payload = build_payload(
        instance_id=instance_id,
        workspace_count=workspace_count,
        signal_count=signal_count,
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, json=payload)
        logger.debug(
            "telemetry: ping sent",
            extra={"status_code": response.status_code, "endpoint": endpoint},
        )
    except Exception:  # pragma: no cover
        # Best-effort: log at DEBUG so it doesn't clutter operator logs.
        logger.debug("telemetry: ping failed (non-fatal)", exc_info=True)


# ---------------------------------------------------------------------------
# Celery task (registered as "telemetry.ping"; beat entry in celery_app.py)
# ---------------------------------------------------------------------------


def register_tasks(celery_app: object) -> None:  # pragma: no cover - called from celery_app.py
    """Attach the ``telemetry.ping`` task to *celery_app*.

    Keeping the task definition here (rather than in a ``tasks.py`` module)
    lets ``telemetry.py`` be a self-contained seam with no module-level Celery
    import — the function is called lazily from ``celery_app.py`` after the app
    object exists.
    """
    import asyncio
    from typing import Any

    # Cast to Any so mypy (which ignores celery stubs) is satisfied without
    # requiring a `type: ignore` on the decorator line.
    _app: Any = celery_app

    @_app.task(name="telemetry.ping")
    def ping_task() -> None:
        """Weekly anonymous telemetry ping — no-op when telemetry is disabled."""
        from civicsignals_api.config import get_settings

        settings = get_settings()
        if not settings.civicsignals_telemetry_enabled:
            logger.debug("telemetry: disabled, skipping ping")
            return

        instance_id = _load_or_create_instance_id(settings.civicsignals_telemetry_id_path)

        loop = asyncio.new_event_loop()
        try:
            workspace_count, signal_count = loop.run_until_complete(_fetch_counts())
            loop.run_until_complete(
                send_ping(
                    endpoint=settings.civicsignals_telemetry_endpoint,
                    instance_id=instance_id,
                    workspace_count=workspace_count,
                    signal_count=signal_count,
                )
            )
        finally:
            loop.close()
