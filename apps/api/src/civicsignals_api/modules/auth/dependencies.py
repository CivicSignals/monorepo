"""Reusable FastAPI auth dependencies (doc 06 §5, doc 08 §1.3, §1.4).

``get_principal`` (B1 + B8) is the bearer-credential seam: it accepts
``Authorization: Bearer <cred>`` where ``<cred>`` is *either* a session/JWT
access token *or* an API token (``cs_live_``/``cs_pat_``, doc 08 §1.3), resolves
it to a :class:`Principal` (user [+ the API token, when present]), and bumps the
token's ``last_used_at`` on use. ``get_current_user`` is the thin adapter over it
that the many endpoints needing only the user depend on; downstream modules import
*these* rather than re-implementing token handling.

``require_workspace`` (B5) is the *workspace-scoping* seam: it resolves the
active workspace from the ``X-Workspace-Id`` header (or the user's
``last_active_workspace_id`` fallback, doc 08 §1.4), validates the caller's
membership, and yields a :class:`WorkspaceContext` (workspace + membership).
Every workspace-scoped module (B6 invites, B7 RBAC, B9 audit, F1 ICP, J1
pipeline, N1 billing, C3 directory) depends on *this* rather than re-reading the
header.

``require_role(min_role)`` (B7) is the *RBAC* seam built on top: it returns a
dependency that yields the same :class:`WorkspaceContext` but first asserts the
caller's membership role is at least ``min_role`` in the workspace role
hierarchy (``owner > admin > member > viewer``), raising RFC 7807 ``403`` when it
is not. Downstream modules gate endpoints by depending on the pre-built aliases:

- :data:`RequireViewer` — any member (reads; viewer-and-up).
- :data:`RequireMember` — read/write workspace data (member-and-up).
- :data:`RequireAdmin` — manage the workspace / members / tokens / billing
  (admin-and-up; owner is implicitly included as the top of the hierarchy).

so e.g. ``async def create_token(ctx: RequireAdmin)`` (B8), ``async def
update_icp(ctx: RequireMember)`` (F1), ``async def list_signals(ctx:
RequireViewer)`` (the feed). A bare ``CurrentWorkspace`` (no role floor) remains
available for endpoints that only need membership, but the convention is to pick
the narrowest role alias the action requires.

``require_scope(scope)`` (B8) is the *API-token scope* seam built on top of
``require_workspace``: it asserts an API-token-authenticated request carries the
named scope (RFC 7807 ``403`` otherwise), while session/JWT callers pass through
on their RBAC role alone (doc 08 §1.3). Workspace API tokens are additionally
pinned to their one workspace by ``require_workspace`` (a mismatched
``X-Workspace-Id`` is ``403``).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
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
from .models import ApiToken

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


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated identity behind a request (B1 JWT + B8 API tokens).

    A request authenticates either with a session/JWT bearer (``api_token`` is
    ``None``) or with an API token (``api_token`` set). Both resolve to a
    :class:`User`. ``api_token`` carries the token's ``scopes`` and bound
    ``workspace_id`` so the scope-gate (:func:`require_scope`) and workspace
    resolution (:func:`require_workspace`) can honour them.
    """

    user: User
    api_token: ApiToken | None = None

    @property
    def scopes(self) -> frozenset[str] | None:
        """The token's granted scopes, or ``None`` for JWT/session auth.

        JWT/session callers are *not* scope-limited (they act with the user's
        full RBAC role, doc 08 §1.3), so :func:`require_scope` waves them through
        and relies on the role check instead. API tokens carry an explicit scope
        set that is always enforced.
        """
        if self.api_token is None:
            return None
        return frozenset(self.api_token.scopes)


async def get_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Principal:
    """Resolve the request principal from the bearer credential, or raise 401.

    Both auth modes (doc 08 §1.3) arrive as ``Authorization: Bearer <cred>``. An
    API-token prefix (``cs_live_``/``cs_pat_``) routes to the API-token path
    (which validates revoke/expiry and bumps ``last_used_at``); everything else
    is treated as a JWT access token.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("missing bearer token")
    credential = credentials.credentials

    if auth_services.looks_like_api_token(credential):
        try:
            authed = await auth_services.authenticate_api_token(session, credential)
        except auth_services.ApiTokenError as exc:
            raise _unauthorized("invalid, revoked, or expired API token") from exc
        return Principal(user=authed.user, api_token=authed.token)

    try:
        user_id = auth_services.verify_token(credential, expected_type="access")
    except auth_services.InvalidTokenError as exc:
        raise _unauthorized("invalid or expired token") from exc
    user = await accounts_services.get_user_by_id(session, user_id)
    if user is None:
        raise _unauthorized("user not found")
    return Principal(user=user)


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


async def get_current_user(principal: CurrentPrincipal) -> User:
    """Resolve the authenticated user from the bearer credential, or raise 401.

    Thin adapter over :func:`get_principal` for the many endpoints that only need
    the user (and not the token's scopes). Accepts both JWTs and API tokens.
    """
    return principal.user


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
    api_token: ApiToken | None = None

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.workspace.id

    @property
    def role(self) -> MembershipRole:
        return self.membership.role

    @property
    def scopes(self) -> frozenset[str] | None:
        """The active API token's scopes, or ``None`` for JWT/session auth."""
        if self.api_token is None:
            return None
        return frozenset(self.api_token.scopes)


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
    principal: CurrentPrincipal,
    session: Annotated[AsyncSession, Depends(get_session)],
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> WorkspaceContext:
    """Resolve + authorize the active workspace for the request (doc 08 §1.4).

    Resolution depends on the auth mode (doc 08 §1.4):

    - **Workspace API token** — the workspace is fixed by the token. A present
      ``X-Workspace-Id`` header must match it (mismatch is ``403``); other
      workspaces are unreachable with that token.
    - **Personal token / session JWT** — the workspace is taken from the
      ``X-Workspace-Id`` header, falling back to the user's
      ``last_active_workspace_id`` when the header is absent.

    Membership is verified through ``accounts.services.get_membership``; a
    missing/invalid header is ``400``, an unresolved or non-member workspace is
    ``404``.

    This only proves *membership*. To additionally require a minimum role, depend
    on :func:`require_role` (or one of the :data:`RequireAdmin` /
    :data:`RequireMember` / :data:`RequireViewer` aliases) instead — they build on
    this and add the role floor (B7). To require a token *scope*, layer
    :func:`require_scope` on top.
    """
    current_user = principal.user
    header_workspace_id: uuid.UUID | None = None
    if x_workspace_id is not None:
        try:
            header_workspace_id = uuid.UUID(x_workspace_id)
        except ValueError as exc:
            raise _bad_workspace_header("X-Workspace-Id is not a valid UUID.") from exc

    token = principal.api_token
    if token is not None and token.workspace_id is not None:
        # Workspace-bound token: the token *is* the workspace selector. A header,
        # if supplied, must agree — a workspace token cannot reach another tenant.
        if header_workspace_id is not None and header_workspace_id != token.workspace_id:
            raise ProblemException(
                status=403,
                code="forbidden",
                title="Workspace mismatch",
                detail=(
                    "This API token is scoped to a single workspace; the "
                    "X-Workspace-Id header does not match it."
                ),
            )
        workspace_id = token.workspace_id
    elif header_workspace_id is not None:
        workspace_id = header_workspace_id
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
    return WorkspaceContext(
        user=current_user,
        workspace=workspace,
        membership=membership,
        api_token=token,
    )


CurrentWorkspace = Annotated[WorkspaceContext, Depends(require_workspace)]


# --- RBAC (B7) --------------------------------------------------------------
# Role hierarchy + a ``require_role`` dependency factory the whole modulith gates
# on. See doc 06 §6 (auth model), doc 08 §1.3 (403 on insufficient role), and the
# threat model §4.1 ("Elevation of Privilege … RBAC enforced in API decorators;
# role stored server-side; no client-side elevation").

# Ordinal rank per role. Higher = more privileged. Membership in the *same*
# workspace is already proven by ``require_workspace``; this layer only compares
# the proven role against the endpoint's floor. ``owner`` is the workspace
# creator and the top of the hierarchy (it can do everything ``admin`` can plus
# ownership-only actions like transfer/delete, gated separately where needed).
_ROLE_RANK: dict[MembershipRole, int] = {
    MembershipRole.VIEWER: 0,
    MembershipRole.MEMBER: 1,
    MembershipRole.ADMIN: 2,
    MembershipRole.OWNER: 3,
}


def role_satisfies(role: MembershipRole, minimum: MembershipRole) -> bool:
    """Return ``True`` if ``role`` is at least ``minimum`` in the hierarchy.

    Pure helper (no DB / request) so services and tests can reason about the
    capability model without a request context. ``owner >= admin >= member >=
    viewer``.
    """
    return _ROLE_RANK[role] >= _ROLE_RANK[minimum]


def _forbidden(minimum: MembershipRole, actual: MembershipRole) -> ProblemException:
    # 403 (not 404): the caller *is* a member of this workspace (they passed
    # ``require_workspace``), so existence is not being leaked — they simply lack
    # the role. doc 08 §1.7: "403 Forbidden — insufficient scope or role".
    return ProblemException(
        status=403,
        code="forbidden",
        title="Insufficient role",
        detail=(
            f"This action requires the '{minimum.value}' role or higher; "
            f"your role in this workspace is '{actual.value}'."
        ),
    )


def require_role(
    min_role: MembershipRole,
) -> Callable[[WorkspaceContext], Awaitable[WorkspaceContext]]:
    """Build a dependency that requires at least ``min_role`` in the workspace.

    The returned dependency reuses :func:`require_workspace` (so workspace
    resolution + membership + 401/400/404 handling are unchanged) and then
    enforces the role floor, raising RFC 7807 ``403`` when the caller's role is
    below ``min_role``. Gate an endpoint by depending on it::

        @router.post("/members")
        async def add_member(ctx: Annotated[WorkspaceContext, Depends(require_role(
            MembershipRole.ADMIN))]) -> ...: ...

    or, more ergonomically, use the :data:`RequireAdmin` / :data:`RequireMember`
    / :data:`RequireViewer` aliases below.
    """

    async def _dependency(ctx: CurrentWorkspace) -> WorkspaceContext:
        if not role_satisfies(ctx.role, min_role):
            raise _forbidden(min_role, ctx.role)
        return ctx

    return _dependency


# Pre-built aliases for the three capability tiers (the ergonomic surface
# downstream modules import). ``viewer`` = read-only, ``member`` = read/write
# workspace data, ``admin`` = manage workspace/members/tokens/billing (doc 06 §6).
RequireViewer = Annotated[WorkspaceContext, Depends(require_role(MembershipRole.VIEWER))]
RequireMember = Annotated[WorkspaceContext, Depends(require_role(MembershipRole.MEMBER))]
RequireAdmin = Annotated[WorkspaceContext, Depends(require_role(MembershipRole.ADMIN))]


# --- API-token scopes (B8) --------------------------------------------------
# The scope-enforcement seam (doc 08 §1.3: "valid token but insufficient scope
# returns 403"; threat-model §4.2: "per-token scopes enforced at the endpoint
# level … checked in require_scope()"). Builds on the resolved
# :class:`WorkspaceContext`, so an endpoint already has membership + role; this
# adds the *token scope* floor.
#
# Session/JWT callers are not scope-limited — they act with the user's full role
# (doc 08 §1.3), so the gate only constrains API-token requests. RBAC
# (``RequireAdmin`` etc.) still applies independently, so a broadly-scoped token
# held by a viewer cannot escalate past their role.


def _insufficient_scope(required: str, granted: frozenset[str]) -> ProblemException:
    return ProblemException(
        status=403,
        code="insufficient_scope",
        title="Insufficient token scope",
        detail=(
            f"This action requires the '{required}' scope; the API token grants "
            f"only: {', '.join(sorted(granted)) or '(none)'}."
        ),
        headers={"WWW-Authenticate": (f'Bearer error="insufficient_scope", scope="{required}"')},
    )


def require_scope(
    scope: str,
) -> Callable[[WorkspaceContext], Awaitable[WorkspaceContext]]:
    """Build a dependency requiring an API token to carry ``scope``.

    Reuses :func:`require_workspace` (so workspace resolution + membership are
    unchanged) and then, *only when the request is API-token authenticated*,
    asserts the token grants ``scope`` — raising RFC 7807 ``403`` otherwise.
    Session/JWT requests pass through (their RBAC role is the gate). Gate an
    endpoint by depending on it::

        @router.get("/signals")
        async def list_signals(
            ctx: Annotated[WorkspaceContext, Depends(require_scope("signals:read"))],
        ) -> ...: ...
    """

    async def _dependency(ctx: CurrentWorkspace) -> WorkspaceContext:
        granted = ctx.scopes
        if granted is not None and scope not in granted:
            raise _insufficient_scope(scope, granted)
        return ctx

    return _dependency
