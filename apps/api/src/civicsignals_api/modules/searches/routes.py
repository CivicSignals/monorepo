"""HTTP endpoints for the searches module, mounted under ``/api/v1/searches`` (H1).

A saved search captures the G1 feed filter set so a user can re-run a filtered
view of their workspace feed and (later, H3) drive a digest off it. Every endpoint
is workspace-scoped through ``require_workspace``: the active workspace
(``X-Workspace-Id``, doc 08 §1.4) scopes every read/write, so workspace A can never
see workspace B's searches.

RBAC (B7): reads use :data:`RequireViewer` (any member); writes use
:data:`RequireMember` (a viewer cannot create/edit/delete). *Within* a workspace,
ownership gates mutation — a member may edit/delete only their **own** searches,
while **shared** searches are visible (read-only) to everyone. The service layer
distinguishes "cannot see it" (``404`` — existence not leaked) from "visible but
not owner" (``403``).

  POST   /searches          — create a saved search (owner = caller)
  GET    /searches          — list searches the caller can see (own + shared), cursor
  GET    /searches/{id}     — get one search the caller can see
  PATCH  /searches/{id}     — rename / re-filter / toggle sharing (owner only)
  DELETE /searches/{id}     — delete (owner only)

Errors are RFC 7807 ``application/problem+json`` (doc 08 §1.7). ``api/v1.py``
already imports and mounts this router — do not add it again there.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import RequireMember, RequireViewer
from civicsignals_api.problems import ProblemException

from . import services
from .models import SavedSearch
from .schemas import (
    SavedSearchCreate,
    SavedSearchOut,
    SavedSearchPage,
    SavedSearchUpdate,
)

router = APIRouter(prefix="/searches", tags=["searches"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]


def _search_out(search: SavedSearch) -> SavedSearchOut:
    return SavedSearchOut.model_validate(search)


def _not_found() -> ProblemException:
    return ProblemException(
        status=status.HTTP_404_NOT_FOUND,
        code="not_found",
        title="Saved search not found",
        detail="No such saved search in this workspace.",
    )


def _forbidden() -> ProblemException:
    return ProblemException(
        status=status.HTTP_403_FORBIDDEN,
        code="forbidden",
        title="Not the owner",
        detail="Only the search's owner may modify or delete it.",
    )


def _bad_cursor() -> ProblemException:
    return ProblemException(
        status=status.HTTP_400_BAD_REQUEST,
        code="bad_request",
        title="Invalid cursor",
        detail="The supplied cursor is malformed.",
    )


@router.post(
    "",
    response_model=SavedSearchOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a saved search",
)
async def create_saved_search(
    body: SavedSearchCreate,
    ctx: RequireMember,
    session: SessionDep,
    response: Response,
) -> SavedSearchOut:
    """Create a saved search owned by the caller in the active workspace (H1)."""
    search = await services.create_saved_search(
        session,
        workspace_id=ctx.workspace_id,
        created_by=ctx.user.id,
        name=body.name,
        filters=body.filters.to_storage(),
        is_shared=body.is_shared,
    )
    await session.commit()
    response.headers["Location"] = f"/api/v1/searches/{search.id}"
    return _search_out(search)


@router.get("", response_model=SavedSearchPage, summary="List saved searches (own + shared)")
async def list_saved_searches(
    ctx: RequireViewer,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> SavedSearchPage:
    """List the caller's own + the workspace's shared searches (cursor-paginated)."""
    try:
        page = await services.list_saved_searches(
            session,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user.id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise _bad_cursor() from exc
    return SavedSearchPage(items=[_search_out(s) for s in page.items], next_cursor=page.next_cursor)


@router.get("/{search_id}", response_model=SavedSearchOut, summary="Get one saved search")
async def get_saved_search(
    search_id: uuid.UUID,
    ctx: RequireViewer,
    session: SessionDep,
) -> SavedSearchOut:
    """Fetch one saved search the caller may see (own or shared), scoped to the workspace.

    A non-existent id, one in another workspace, or another member's *private*
    search is ``404`` (existence is not leaked across the tenant / owner boundary).
    """
    search = await services.get_saved_search(
        session, workspace_id=ctx.workspace_id, user_id=ctx.user.id, search_id=search_id
    )
    if search is None:
        raise _not_found()
    return _search_out(search)


@router.patch("/{search_id}", response_model=SavedSearchOut, summary="Update a saved search")
async def update_saved_search(
    search_id: uuid.UUID,
    body: SavedSearchUpdate,
    ctx: RequireMember,
    session: SessionDep,
) -> SavedSearchOut:
    """Rename / re-filter / toggle sharing on a search the caller owns (H1)."""
    try:
        search = await services.update_saved_search(
            session,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user.id,
            search_id=search_id,
            name=body.name,
            filters=body.filters.to_storage() if body.filters is not None else None,
            is_shared=body.is_shared,
        )
        await session.commit()
    except services.SavedSearchNotFoundError as exc:
        await session.rollback()
        raise _not_found() from exc
    except services.SavedSearchForbiddenError as exc:
        await session.rollback()
        raise _forbidden() from exc
    return _search_out(search)


@router.delete(
    "/{search_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a saved search",
)
async def delete_saved_search(
    search_id: uuid.UUID,
    ctx: RequireMember,
    session: SessionDep,
) -> Response:
    """Delete a saved search the caller owns (H1)."""
    try:
        await services.delete_saved_search(
            session, workspace_id=ctx.workspace_id, user_id=ctx.user.id, search_id=search_id
        )
        await session.commit()
    except services.SavedSearchNotFoundError as exc:
        await session.rollback()
        raise _not_found() from exc
    except services.SavedSearchForbiddenError as exc:
        await session.rollback()
        raise _forbidden() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
