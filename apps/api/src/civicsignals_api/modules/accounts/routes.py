"""HTTP endpoints for the accounts module (doc 08 §2).

The accounts module owns two URL spaces:

- ``/accounts`` — reserved for account-level endpoints (kept as the module's
  namespace; member/profile endpoints land here in later epics).
- ``/workspaces`` — workspace creation, listing, lookup, and switching (B5,
  doc 08 §2 ``/workspaces  list, create, switch``). These are mounted top-level
  per the spec rather than under ``/accounts``.

The module's exported ``router`` aggregates both so ``api/v1.py`` mounts the
whole accounts surface with a single ``include_router`` (no v1.py change for B5,
which only adds endpoints to an already-mounted module).

Workspaces are the tenant boundary every workspace-scoped module scopes through;
the ``X-Workspace-Id`` header (doc 08 §1.4) names one of these. Errors are RFC
7807 ``application/problem+json`` (doc 08 §1.7) raised as :class:`ProblemException`.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import CurrentUser
from civicsignals_api.problems import ProblemException

from . import services
from .models import MembershipRole, Workspace
from .schemas import WorkspaceCreate, WorkspaceOut, WorkspacePage

# Module namespace (account-level endpoints attach here in later epics).
router = APIRouter()
accounts_router = APIRouter(prefix="/accounts", tags=["accounts"])
workspaces_router = APIRouter(prefix="/workspaces", tags=["workspaces"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]


def _workspace_out(workspace: Workspace, role: MembershipRole | None = None) -> WorkspaceOut:
    out = WorkspaceOut.model_validate(workspace)
    if role is not None:
        out = out.model_copy(update={"role": role})
    return out


@workspaces_router.post(
    "",
    response_model=WorkspaceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace",
)
async def create_workspace(
    body: WorkspaceCreate,
    current_user: CurrentUser,
    session: SessionDep,
    response: Response,
) -> WorkspaceOut:
    """Create a workspace owned by the caller (who becomes its ``owner`` member)."""
    try:
        workspace = await services.create_workspace(
            session,
            owner=current_user,
            name=body.name,
            slug=body.slug,
            country_default=body.country_default,
        )
        await session.commit()
    except services.SlugConflictError as exc:
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="slug_taken",
            title="Workspace slug unavailable",
            detail="Could not allocate a unique slug; supply a different one.",
        ) from exc
    except IntegrityError as exc:  # explicit-slug collision race
        await session.rollback()
        raise ProblemException(
            status=status.HTTP_409_CONFLICT,
            code="slug_taken",
            title="Workspace slug unavailable",
            detail="A workspace with this slug already exists.",
        ) from exc

    response.headers["Location"] = f"/api/v1/workspaces/{workspace.id}"
    return _workspace_out(workspace, MembershipRole.OWNER)


@workspaces_router.get(
    "",
    response_model=WorkspacePage,
    summary="List the workspaces I belong to",
)
async def list_workspaces(
    current_user: CurrentUser,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> WorkspacePage:
    """List the caller's workspaces (cursor-paginated, doc 06 §5)."""
    try:
        page = await services.list_workspaces_for_user(
            session, current_user.id, cursor=cursor, limit=limit
        )
    except ValueError as exc:
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            title="Invalid cursor",
            detail="The supplied cursor is malformed.",
        ) from exc
    roles = await services.list_roles_for_user(session, current_user.id, [w.id for w in page.items])
    return WorkspacePage(
        items=[_workspace_out(w, roles.get(w.id)) for w in page.items],
        next_cursor=page.next_cursor,
    )


@workspaces_router.get(
    "/{workspace_id}",
    response_model=WorkspaceOut,
    summary="Get one workspace I belong to",
)
async def get_workspace(
    workspace_id: uuid.UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> WorkspaceOut:
    """Fetch a workspace the caller is a member of.

    Non-members get ``404`` (not ``403``) so a workspace's existence is not
    leaked across tenants (doc 08 §1.7 table: ``404 Not found (or hidden from
    this workspace)``).
    """
    membership = await services.get_membership(
        session, workspace_id=workspace_id, user_id=current_user.id
    )
    if membership is None:
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Workspace not found",
            detail="No such workspace, or you are not a member.",
        )
    workspace = await services.get_workspace(session, workspace_id)
    if workspace is None:  # pragma: no cover - membership implies a live workspace
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Workspace not found",
            detail="No such workspace, or you are not a member.",
        )
    return _workspace_out(workspace, membership.role)


@workspaces_router.post(
    "/{workspace_id}/switch",
    response_model=WorkspaceOut,
    summary="Switch the active workspace",
)
async def switch_workspace(
    workspace_id: uuid.UUID,
    current_user: CurrentUser,
    session: SessionDep,
) -> WorkspaceOut:
    """Set the caller's ``last_active_workspace_id`` to this workspace.

    This is the fallback the API scopes to when ``X-Workspace-Id`` is absent
    (doc 08 §1.4). Non-members get ``404`` (existence not leaked).
    """
    membership = await services.get_membership(
        session, workspace_id=workspace_id, user_id=current_user.id
    )
    if membership is None:
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Workspace not found",
            detail="No such workspace, or you are not a member.",
        )
    await services.set_last_active_workspace(session, user=current_user, workspace_id=workspace_id)
    await session.commit()
    workspace = await services.get_workspace(session, workspace_id)
    assert workspace is not None  # membership guarantees a live workspace
    return _workspace_out(workspace, membership.role)


router.include_router(accounts_router)
router.include_router(workspaces_router)
