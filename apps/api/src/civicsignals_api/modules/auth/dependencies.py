"""Reusable FastAPI auth dependencies (doc 06 §5, doc 08 §1.3, §1.4).

``get_current_user`` is the shared seam every protected endpoint in every module
depends on: it pulls the bearer token (``Authorization: Bearer <jwt>``), verifies
it via ``auth.services.verify_token``, and loads the user through
``accounts.services``. Downstream modules import *this* dependency rather than
re-implementing token handling.

``require_workspace`` (B5) is the *workspace-scoping* seam: it resolves the
active workspace from the ``X-Workspace-Id`` header (or the user's
``last_active_workspace_id`` fallback, doc 08 §1.4), validates the caller's
membership, and yields a :class:`WorkspaceContext` (workspace + membership).
Every workspace-scoped module (B6 invites, B7 RBAC, B9 audit, F1 ICP, J1
pipeline, N1 billing, C3 directory) depends on *this* rather than re-reading the
header. RBAC role enforcement layers on in B7 (the role is already on the
yielded membership).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import Membership, MembershipRole, User, Workspace
from civicsignals_api.problems import ProblemException

from . import services as auth_services

# ``auto_error=False`` so a missing header yields our RFC 7807 problem rather
# than FastAPI's default JSON; the OpenAPI security scheme is still advertised.
_bearer = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


def _unauthorized(detail: str) -> ProblemException:
    return ProblemException(
        status=401,
        code="unauthorized",
        title="Unauthorized",
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> User:
    """Resolve the authenticated user from the bearer token, or raise 401."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("missing bearer token")
    try:
        user_id = auth_services.verify_token(credentials.credentials, expected_type="access")
    except auth_services.InvalidTokenError as exc:
        raise _unauthorized("invalid or expired token") from exc

    user = await accounts_services.get_user_by_id(session, user_id)
    if user is None:
        raise _unauthorized("user not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """The resolved active workspace for a request (B5, doc 08 §1.4).

    ``membership`` carries the caller's ``role`` so B7 can enforce RBAC without
    a second query. ``workspace_id`` / ``role`` are convenience accessors callers
    scope their queries / checks on.
    """

    user: User
    workspace: Workspace
    membership: Membership

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.workspace.id

    @property
    def role(self) -> MembershipRole:
        return self.membership.role


def _bad_workspace_header(detail: str) -> ProblemException:
    return ProblemException(
        status=400,
        code="bad_request",
        title="Invalid workspace header",
        detail=detail,
    )


def _no_workspace() -> ProblemException:
    # 404 (not 403) so a workspace's existence is not leaked to non-members
    # (doc 08 §1.7: "404 Not found (or hidden from this workspace)").
    return ProblemException(
        status=404,
        code="not_found",
        title="Workspace not found",
        detail="No such workspace, or you are not a member.",
    )


async def require_workspace(
    current_user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> WorkspaceContext:
    """Resolve + authorize the active workspace for the request (doc 08 §1.4).

    The workspace is taken from the ``X-Workspace-Id`` header, falling back to
    the user's ``last_active_workspace_id`` when the header is absent (the
    documented session/PAT behaviour). Membership is verified through
    ``accounts.services.get_membership``; a missing/invalid header is ``400``, an
    unresolved or non-member workspace is ``404``.

    # TODO B7: gate role-restricted actions on ``ctx.role`` (the membership role
    #   is already attached here, so RBAC enforcement is a pure add-on).
    """
    if x_workspace_id is not None:
        try:
            workspace_id = uuid.UUID(x_workspace_id)
        except ValueError as exc:
            raise _bad_workspace_header("X-Workspace-Id is not a valid UUID.") from exc
    elif current_user.last_active_workspace_id is not None:
        workspace_id = current_user.last_active_workspace_id
    else:
        raise _bad_workspace_header(
            "No X-Workspace-Id header and no last active workspace; "
            "create or switch to a workspace first."
        )

    membership = await accounts_services.get_membership(
        session, workspace_id=workspace_id, user_id=current_user.id
    )
    if membership is None:
        raise _no_workspace()
    workspace = await accounts_services.get_workspace(session, workspace_id)
    if workspace is None:  # pragma: no cover - membership implies a live workspace
        raise _no_workspace()
    return WorkspaceContext(user=current_user, workspace=workspace, membership=membership)


CurrentWorkspace = Annotated[WorkspaceContext, Depends(require_workspace)]
