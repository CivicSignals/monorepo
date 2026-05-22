"""Public service interface for the admin module.

Other modules call admin only through the functions defined here — never by
importing admin's models or routes directly (doc 06 §3).

B9 — Audit log
--------------
:func:`record_audit_event` is the primary write surface: a fire-and-forget
append that persists one :class:`.models.AuditEvent` row and flushes (but does
NOT commit — the caller owns the transaction boundary).

:func:`list_audit_events` is the paginated read surface used by the admin API.
"""

from __future__ import annotations

import base64
import binascii
import uuid as _uuid_module
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditEvent

# ---------------------------------------------------------------------------
# Cursor pagination helpers (same keyset-on-UUID-v7 pattern as accounts)
# ---------------------------------------------------------------------------

DEFAULT_LIMIT: int = 25
MAX_LIMIT: int = 100


def _encode_cursor(row_id: UUID) -> str:
    return base64.urlsafe_b64encode(row_id.bytes).decode("ascii")


def _decode_cursor(cursor: str) -> UUID:
    try:
        return _uuid_module.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


@dataclass(slots=True)
class AuditPage:
    """A cursor-paginated page of audit events."""

    items: list[AuditEvent]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def record_audit_event(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: UUID | None = None,
    actor_token_id: UUID | None = None,
    workspace_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, object] | None = None,
    ip: str | None = None,
) -> AuditEvent:
    """Append one audit event row and flush (caller commits).

    This is the **only** write path for the audit log — no other module inserts
    into ``admin_audit_event`` directly. Other modules call this function (via
    the cross-module service rule) or emit events that the bus listener below
    converts into calls here.

    The caller is responsible for committing the surrounding transaction.  When
    called from an event-bus listener the listener should open (and commit) its
    own session so the audit write is independent of the domain transaction.
    """
    event = AuditEvent(
        action=action,
        actor_user_id=actor_user_id,
        actor_token_id=actor_token_id,
        workspace_id=workspace_id,
        target_type=target_type,
        target_id=target_id,
        metadata_=dict(metadata) if metadata else {},
        ip=ip,
    )
    session.add(event)
    await session.flush()
    return event


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def list_audit_events(
    session: AsyncSession,
    workspace_id: UUID,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    action: str | None = None,
    actor_user_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> AuditPage:
    """Return a cursor-paginated page of audit events for ``workspace_id``.

    Ordered by ``occurred_at DESC, id DESC`` (newest first).  All filters are
    AND-combined; omit them to get the full log.

    ``cursor`` is an opaque base64-encoded UUID v7 that points to the *last row
    of the previous page* — i.e. the caller passes back ``next_cursor`` verbatim.
    """
    limit = max(1, min(limit, MAX_LIMIT))

    stmt = (
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace_id)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
    )

    if action is not None:
        stmt = stmt.where(AuditEvent.action == action)
    if actor_user_id is not None:
        stmt = stmt.where(AuditEvent.actor_user_id == actor_user_id)
    if since is not None:
        stmt = stmt.where(AuditEvent.occurred_at >= since)
    if until is not None:
        stmt = stmt.where(AuditEvent.occurred_at <= until)

    # Keyset cursor: events *before* the cursor row (older, since DESC order).
    # We compare on (occurred_at DESC, id DESC), so "next page" means
    # occurred_at <= cursor_occurred_at AND (occurred_at < … OR id < …).
    # For simplicity we use id as the sole cursor column (UUID v7 is
    # time-ordered so the ordering is consistent with occurred_at DESC).
    if cursor is not None:
        cursor_id = _decode_cursor(cursor)
        stmt = stmt.where(AuditEvent.id < cursor_id)

    stmt = stmt.limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = _encode_cursor(items[-1].id) if has_more and items else None
    return AuditPage(items=items, next_cursor=next_cursor)
