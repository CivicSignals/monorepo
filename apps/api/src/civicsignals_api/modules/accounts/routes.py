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

import structlog
from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api import events
from civicsignals_api.db import get_session
from civicsignals_api.modules.auth import services as auth_services
from civicsignals_api.modules.auth.dependencies import (
    CurrentUser,
    RequireAdmin,
    WorkspaceContext,
)
from civicsignals_api.modules.auth.models import ApiToken, ApiTokenType
from civicsignals_api.modules.auth.schemas import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenList,
    ApiTokenOut,
)
from civicsignals_api.problems import ProblemException

from . import services
from .models import MembershipRole, Workspace
from .schemas import (
    MemberOut,
    MemberPage,
    MemberRoleUpdate,
    WorkspaceCreate,
    WorkspaceOut,
    WorkspacePage,
)

logger = structlog.get_logger(__name__)

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


# --- Member management (B7 RBAC) -------------------------------------------
# These endpoints are workspace-scoped via the path (``/workspaces/{id}/members``,
# doc 08 §2) AND admin-gated. They use ``RequireAdmin`` to resolve the active
# workspace (header/last-active) and assert the caller is an admin there, then
# require the path ``workspace_id`` to match — so the active-workspace context and
# the addressed workspace can't diverge. This is the canonical admin-gated CRUD
# pattern other modules (B8 tokens, K1 integrations) copy.


def _require_active_workspace_matches_path(ctx: WorkspaceContext, workspace_id: uuid.UUID) -> None:
    """403 if the path workspace differs from the RBAC-resolved active workspace.

    The role floor is already enforced by ``RequireAdmin`` against the active
    workspace; this guards against addressing a *different* workspace via the path
    than the one the role was checked in.
    """
    if ctx.workspace_id != workspace_id:
        raise ProblemException(
            status=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            title="Workspace mismatch",
            detail="The path workspace does not match the active (X-Workspace-Id) workspace.",
        )


@workspaces_router.get(
    "/{workspace_id}/members",
    response_model=MemberPage,
    summary="List workspace members (admin only)",
)
async def list_members(
    workspace_id: uuid.UUID,
    ctx: RequireAdmin,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> MemberPage:
    """List the members of a workspace. Requires the ``admin`` role (doc 06 §6)."""
    _require_active_workspace_matches_path(ctx, workspace_id)
    try:
        page = await services.list_members(session, workspace_id, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            title="Invalid cursor",
            detail="The supplied cursor is malformed.",
        ) from exc
    return MemberPage(
        items=[MemberOut.model_validate(m) for m in page.items],
        next_cursor=page.next_cursor,
    )


@workspaces_router.patch(
    "/{workspace_id}/members/{user_id}",
    response_model=MemberOut,
    summary="Change a member's role (admin only)",
)
async def update_member_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberRoleUpdate,
    ctx: RequireAdmin,
    session: SessionDep,
) -> MemberOut:
    """Set a member's role to admin/member/viewer. Requires the ``admin`` role.

    The workspace ``owner``'s role cannot be changed here (ownership is a single
    seat); attempting it returns ``403``. A non-member target is ``404``.
    """
    _require_active_workspace_matches_path(ctx, workspace_id)
    target = await services.get_membership(session, workspace_id=workspace_id, user_id=user_id)
    if target is None:
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Member not found",
            detail="No such member in this workspace.",
        )
    if target.role is MembershipRole.OWNER:
        raise ProblemException(
            status=status.HTTP_403_FORBIDDEN,
            code="forbidden",
            title="Cannot change owner role",
            detail="The workspace owner's role cannot be changed.",
        )
    old_role = target.role
    await services.set_member_role(session, membership=target, role=body.role)
    await session.commit()
    # B9: emit audit event for role change (best-effort).
    try:
        await events.publish(
            events.MEMBER_ROLE_CHANGED,
            {
                "user_id": str(ctx.user.id),
                "workspace_id": str(workspace_id),
                "target_user_id": str(user_id),
                "old_role": old_role.value,
                "new_role": body.role.value,
            },
        )
    except Exception:
        logger.warning("member_role_changed_event_failed", workspace_id=str(workspace_id))
    return MemberOut.model_validate(target)


# --- Workspace API tokens (B8) ----------------------------------------------
# Server-to-server integration tokens scoped to one workspace (doc 08 §1.3,
# resource map ``/workspaces/{id}/api-tokens``). Issuing/listing/revoking is an
# *admin* capability (``RequireAdmin``); the secret is revealed once on create
# and never again (threat-model §4.2). Personal access tokens (acting as the
# user across workspaces) live under ``/auth/tokens`` in the auth module.


def _bad_token_scopes(invalid: list[str]) -> ProblemException:
    return ProblemException(
        status=422,
        code="invalid_scope",
        title="Unknown token scope",
        detail=f"Unknown scope(s): {', '.join(invalid)}.",
        errors=[{"field": "scopes", "code": "unknown_scope", "message": s} for s in invalid],
    )


@workspaces_router.post(
    "/{workspace_id}/api-tokens",
    response_model=ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace API token (admin only, revealed once)",
)
async def create_api_token(
    workspace_id: uuid.UUID,
    body: ApiTokenCreate,
    ctx: RequireAdmin,
    session: SessionDep,
    response: Response,
) -> ApiTokenCreated:
    """Mint a workspace-scoped API token; the plaintext is returned **once**.

    Requires the ``admin`` role (doc 06 §6). The token is bound to this
    workspace and cannot reach any other tenant. The secret in ``token`` is never
    retrievable again.
    """
    _require_active_workspace_matches_path(ctx, workspace_id)
    try:
        issued = await auth_services.create_api_token(
            session,
            token_type=ApiTokenType.WORKSPACE,
            name=body.name,
            scopes=body.scopes,
            user_id=ctx.user.id,
            created_by_user_id=ctx.user.id,
            workspace_id=workspace_id,
            expires_at=body.expires_at,
        )
        await session.commit()
    except auth_services.InvalidScopeError as exc:
        await session.rollback()
        raise _bad_token_scopes(exc.invalid) from exc

    response.headers["Location"] = f"/api/v1/workspaces/{workspace_id}/api-tokens/{issued.token.id}"
    out = ApiTokenOut.model_validate(issued.token)
    return ApiTokenCreated(**out.model_dump(), token=issued.plaintext)


@workspaces_router.get(
    "/{workspace_id}/api-tokens",
    response_model=ApiTokenList,
    summary="List workspace API tokens (admin only, no secrets)",
)
async def list_api_tokens(
    workspace_id: uuid.UUID, ctx: RequireAdmin, session: SessionDep
) -> ApiTokenList:
    """List the workspace's API tokens as metadata only — secrets never returned."""
    _require_active_workspace_matches_path(ctx, workspace_id)
    tokens = await auth_services.list_workspace_api_tokens(session, workspace_id)
    return ApiTokenList(items=[ApiTokenOut.model_validate(t) for t in tokens])


def _resolve_workspace_token(token: ApiToken | None, workspace_id: uuid.UUID) -> ApiToken:
    """Resolve a workspace token belonging to ``workspace_id`` or raise ``404``."""
    if (
        token is None
        or token.token_type is not ApiTokenType.WORKSPACE
        or token.workspace_id != workspace_id
    ):
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Token not found",
            detail="No such API token in this workspace.",
        )
    return token


@workspaces_router.delete(
    "/{workspace_id}/api-tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a workspace API token (admin only)",
)
async def revoke_api_token(
    workspace_id: uuid.UUID,
    token_id: uuid.UUID,
    ctx: RequireAdmin,
    session: SessionDep,
) -> Response:
    """Revoke a workspace API token. Requires ``admin``; idempotent, immediate."""
    _require_active_workspace_matches_path(ctx, workspace_id)
    token = _resolve_workspace_token(
        await auth_services.get_api_token(session, token_id), workspace_id
    )
    await auth_services.revoke_api_token(session, token)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


router.include_router(accounts_router)
router.include_router(workspaces_router)
