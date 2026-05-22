"""HTTP endpoints for the entities module, mounted under `/api/v1/entities`.

The entity directory is the **public, read-only** account universe (doc 07 §3 —
entities are global, not workspace-scoped), so these endpoints intentionally do
**not** require the ``X-Workspace-Id`` header (C1 req 5). They expose list /
search / get / children, all with **cursor** pagination (doc 06 §5). Writes
happen only through seeding (C1) and future ingestion (doc 18), never via HTTP.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.ratelimit import public_directory_limiter

from . import services
from .schemas import EntityPage, EntityRead

router = APIRouter(prefix="/entities", tags=["entities"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]
# P4: the entity directory is the public, unauthenticated account universe (C1
# req 5, doc 07 §3) that the /directory + /s pages read. Per-client (IP) rate
# limit it to throttle scrapers while staying generous for humans and polite
# crawlers (see civicsignals_api.ratelimit). All three reads share one bucket.
PublicRateLimit = Depends(public_directory_limiter)


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    """RFC 7807 ``application/problem+json`` response (doc 06 §5).

    Returning a ``Response`` makes FastAPI skip ``response_model`` validation, so
    the error body is sent verbatim rather than coerced into ``EntityRead``.
    """
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


@router.get("", response_model=EntityPage, summary="List/search entities")
async def list_entities(
    session: SessionDep,
    _rate_limit: Annotated[None, PublicRateLimit],
    q: Annotated[str | None, Query(description="Name substring search.")] = None,
    type: Annotated[list[str] | None, Query(description="Filter by entity type slug.")] = None,
    kind: Annotated[list[str] | None, Query(description="Filter by kind slug.")] = None,
    state: Annotated[list[str] | None, Query(description="Filter by USPS state code.")] = None,
    geo_id: Annotated[uuid.UUID | None, Query(description="Filter by geography id.")] = None,
    status: Annotated[str | None, Query(description="active|dissolved|merged.")] = None,
    parent_id: Annotated[uuid.UUID | None, Query(description="Filter by parent entity id.")] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> EntityPage | JSONResponse:
    filters = services.EntityFilters(
        q=q,
        types=type or (),
        kind_slugs=kind or (),
        states=state or (),
        geo_id=geo_id,
        status=status,
        parent_id=parent_id,
    )
    try:
        page = await services.search_entities(session, filters, cursor=cursor, limit=limit)
    except ValueError:
        return _problem(400, "Invalid cursor", "The supplied cursor is malformed.")
    return EntityPage(
        items=[EntityRead.model_validate(e) for e in page.items],
        next_cursor=page.next_cursor,
    )


@router.get("/{entity_id}", response_model=EntityRead, summary="Get one entity")
async def get_entity(
    entity_id: uuid.UUID,
    session: SessionDep,
    _rate_limit: Annotated[None, PublicRateLimit],
) -> EntityRead | JSONResponse:
    entity = await services.get_entity(session, entity_id)
    if entity is None:
        return _problem(404, "Entity not found", f"No entity with id {entity_id}.")
    return EntityRead.model_validate(entity)


@router.get(
    "/{entity_id}/children",
    response_model=EntityPage,
    summary="List an entity's direct children",
)
async def list_entity_children(
    entity_id: uuid.UUID,
    session: SessionDep,
    _rate_limit: Annotated[None, PublicRateLimit],
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> EntityPage:
    page = await services.list_children(session, entity_id, cursor=cursor, limit=limit)
    return EntityPage(
        items=[EntityRead.model_validate(e) for e in page.items],
        next_cursor=page.next_cursor,
    )
