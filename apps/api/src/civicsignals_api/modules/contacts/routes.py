"""HTTP endpoints for the contacts module, mounted under ``/api/v1/contacts`` (C2 req 3).

Contacts are **global** (doc 07 §3 — "Contacts are global per Entity. We never
store workspace-private contact records."), so these endpoints intentionally do
**not** require the ``X-Workspace-Id`` header (same reasoning as entities module).

Exposed read endpoints:
- ``GET /contacts``             — list/search contacts (optionally filtered by entity_id).
- ``GET /contacts/{contact_id}``— get one contact by id.

Writes happen only via ``contacts/services.py`` (called by ingestion/C4/C6); there
is no public HTTP write surface in this task scope.

Cursor pagination: ``?cursor=…&limit=25`` (never offset), per doc 06 §5.
RFC 7807 ``application/problem+json`` errors (doc 06 §5).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session

from . import services
from .models import Contact
from .schemas import ContactPage, ContactRead

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
