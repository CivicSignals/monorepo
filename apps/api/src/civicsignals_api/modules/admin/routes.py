"""HTTP endpoints for the admin module, mounted under ``/api/v1/admin``.

B9 — Audit log read API.

Append-only: no write or delete endpoints are exposed here.  The only way to
add rows is via :func:`~civicsignals_api.modules.admin.services.record_audit_event`
(service layer) or the in-process event bus listener.

All endpoints require the ``admin`` role or higher (``RequireAdmin``).  Audit
events are scoped to the active workspace resolved by ``RequireAdmin`` so one
workspace cannot see another's events.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import RequireAdmin
from civicsignals_api.problems import ProblemException

from . import services
from .schemas import AuditEventOut, AuditEventPage

router = APIRouter(prefix="/admin", tags=["admin"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]


@router.get(
    "/audit-events",
    response_model=AuditEventPage,
    summary="List audit events for the active workspace (admin only)",
)
async def list_audit_events(
    ctx: RequireAdmin,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
    action: Annotated[str | None, Query(description="Filter by action string.")] = None,
    actor_user_id: Annotated[
        uuid.UUID | None, Query(description="Filter by actor user id.")
    ] = None,
    since: Annotated[
        datetime | None, Query(description="Filter events at or after this ISO-8601 timestamp.")
    ] = None,
    until: Annotated[
        datetime | None, Query(description="Filter events at or before this ISO-8601 timestamp.")
    ] = None,
) -> AuditEventPage:
    """Return a cursor-paginated, newest-first list of audit events.

    Scoped to the active workspace (resolved from ``X-Workspace-Id`` or the
    user's last-active workspace).  Requires the ``admin`` role.

    Optional query filters:

    - ``action`` — exact match on the action string (e.g. ``auth.login``).
    - ``actor_user_id`` — filter to events by a specific actor.
    - ``since`` / ``until`` — ISO-8601 timestamps bounding ``occurred_at``.
    """
    try:
        page = await services.list_audit_events(
            session,
            ctx.workspace_id,
            cursor=cursor,
            limit=limit,
            action=action,
            actor_user_id=actor_user_id,
            since=since,
            until=until,
        )
    except ValueError as exc:
        raise ProblemException(
            status=400,
            code="bad_request",
            title="Invalid cursor",
            detail="The supplied cursor is malformed.",
        ) from exc

    return AuditEventPage(
        items=[AuditEventOut.from_orm_event(e) for e in page.items],
        next_cursor=page.next_cursor,
    )
