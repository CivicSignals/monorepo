"""Reusable FastAPI auth dependencies (doc 06 §5, doc 08 §1.3).

``get_current_user`` is the shared seam every protected endpoint in every module
depends on: it pulls the bearer token (``Authorization: Bearer <jwt>``), verifies
it via ``auth.services.verify_token``, and loads the user through
``accounts.services``. Downstream modules import *this* dependency rather than
re-implementing token handling.

Workspace scoping (``X-Workspace-Id``) and RBAC are deliberately *not* done here
— they layer on in B5/B7. The seams are marked below.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import User
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
    # B5: ``X-Workspace-Id`` selects the active workspace for session/PAT auth;
    # validated against the user's membership set there. Accepted now so the
    # contract is stable; unused until B5.
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> User:
    """Resolve the authenticated user from the bearer token, or raise 401.

    # TODO B5: resolve + validate ``x_workspace_id`` against membership and set
    #   the request workspace context here.
    # TODO B7: attach the user's role for the active workspace for RBAC checks.
    """
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
