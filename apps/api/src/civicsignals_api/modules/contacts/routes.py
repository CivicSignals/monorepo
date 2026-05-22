"""HTTP endpoints for the contacts module, mounted under ``/api/v1/contacts`` (C2 req 3, C6).

Contacts are **global** (doc 07 §3 — "Contacts are global per Entity. We never
store workspace-private contact records."), so read endpoints intentionally do
**not** require the ``X-Workspace-Id`` header (same reasoning as entities module).

Exposed endpoints:
- ``GET  /contacts``                             — list/search contacts (optionally by entity_id).
- ``GET  /contacts/{contact_id}``                — get one contact by id.
- ``POST /contacts/{contact_id}/report-invalid`` — workspace-scoped correction report (C6).
  Requires ``X-Workspace-Id`` + bearer auth. Records who reported the contact as
  invalid/bounced/wrong and why; updates the contact's status/verified/confidence.
  This is the manual-report seam; K5 (push-failure recovery) calls the underlying
  service function directly.

Cursor pagination: ``?cursor=…&limit=25`` (never offset), per doc 06 §5.
RFC 7807 ``application/problem+json`` errors (doc 06 §5).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import RequireMember

from . import services
from .models import Contact
from .schemas import (
    ContactCorrectionRead,
    ContactCorrectionRequest,
    ContactCorrectionResponse,
    ContactPage,
    ContactRead,
)

router = APIRouter(prefix="/contacts", tags=["contacts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    """RFC 7807 ``application/problem+json`` response (doc 06 §5)."""
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


@router.get("", response_model=ContactPage, summary="List contacts (optionally by entity)")
async def list_contacts(
    session: SessionDep,
    entity_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter contacts to a single entity."),
    ] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> ContactPage | JSONResponse:
    """Return a cursor-paginated list of contacts.

    When ``entity_id`` is supplied only contacts belonging to that entity are
    returned. Without it the endpoint returns all contacts (global directory),
    which is useful for admin tooling; production callers should always supply
    ``entity_id``.
    """
    if entity_id is None:
        # Full-table scan without entity filter: return first page only.
        # Callers should supply entity_id for production use.
        from sqlalchemy import select

        stmt = select(Contact)
        if cursor is not None:
            try:
                stmt = stmt.where(Contact.id > services.decode_cursor(cursor))
            except ValueError:
                return _problem(400, "Invalid cursor", "The supplied cursor is malformed.")
        stmt = stmt.order_by(Contact.id).limit(limit + 1)
        rows = list((await session.execute(stmt)).scalars().all())
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = services.encode_cursor(items[-1].id) if has_more and items else None
        page = services.ContactPage(items=list(items), next_cursor=next_cursor)
    else:
        try:
            page = await services.list_contacts_for_entity(
                session, entity_id, cursor=cursor, limit=limit
            )
        except ValueError:
            return _problem(400, "Invalid cursor", "The supplied cursor is malformed.")

    return ContactPage(
        items=[ContactRead.model_validate(c) for c in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{contact_id}", response_model=ContactRead, summary="Get one contact")
async def get_contact(
    contact_id: uuid.UUID,
    session: SessionDep,
) -> ContactRead | JSONResponse:
    contact = await services.get_contact(session, contact_id)
    if contact is None:
        return _problem(404, "Contact not found", f"No contact with id {contact_id}.")
    return ContactRead.model_validate(contact)


@router.post(
    "/{contact_id}/report-invalid",
    response_model=ContactCorrectionResponse,
    status_code=201,
    summary="Report a contact as invalid / bounced (C6)",
)
async def report_contact_invalid(
    contact_id: uuid.UUID,
    body: ContactCorrectionRequest,
    session: SessionDep,
    ctx: RequireMember,
) -> ContactCorrectionResponse | JSONResponse:
    """Record a workspace-scoped correction report for a contact.

    This endpoint is behind ``require_workspace`` (via :data:`RequireMember`):
    - The ``X-Workspace-Id`` header (or ``last_active_workspace_id`` fallback) must
      identify the caller's workspace.
    - The caller must be at least a ``member`` in that workspace.

    On success:
    - A new :class:`~contacts.models.ContactCorrection` audit row is inserted.
    - The contact's ``status`` is set to ``"bounced"`` or ``"invalid"``.
    - ``verified`` is cleared; ``confidence`` is lowered; ``reported_invalid_at``
      and ``bounce_count`` are updated.
    - The updated contact + new correction row are returned with HTTP 201.

    Calling this endpoint multiple times is safe (idempotent-friendly): each call
    appends an audit row and further lowers confidence. The contact status does not
    change again if it is already ``"bounced"``/``"invalid"``.
    """
    try:
        result = await services.report_contact_invalid(
            session,
            services.CorrectionInput(
                contact_id=contact_id,
                workspace_id=ctx.workspace_id,
                reporter_id=ctx.user.id,
                kind=body.kind,
                reason=body.reason,
                correction=body.correction,
            ),
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        msg = str(exc)
        if "not found" in msg.lower():
            return _problem(404, "Contact not found", f"No contact with id {contact_id}.")
        return _problem(400, "Invalid request", msg)
    except IntegrityError:
        await session.rollback()
        return _problem(409, "Conflict", "Could not save the correction report; please retry.")

    return ContactCorrectionResponse(
        contact=ContactRead.model_validate(result.contact),
        correction=ContactCorrectionRead.model_validate(result.correction),
    )
