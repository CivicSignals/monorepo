"""Pydantic request/response shapes for the accounts module (doc 06 §3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from .models import MembershipRole


class UserOut(BaseModel):
    """Public representation of a user (doc 08 §3.1 ``user`` object)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    name: str | None = None
    email_verified: bool
    created_at: datetime


# --- Workspaces (B5) --------------------------------------------------------


class WorkspaceCreate(BaseModel):
    """Request body for ``POST /workspaces`` (doc 08 §2 ``/workspaces``).

    ``slug`` is optional — when omitted the server derives a unique one from the
    name. ``country_default`` is the workspace's default jurisdiction (doc 07).
    """

    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=48, pattern=r"^[a-z0-9-]+$")
    country_default: str = Field(default="US", min_length=2, max_length=2)


class WorkspaceOut(BaseModel):
    """Public representation of a workspace.

    ``role`` is the requesting user's role in this workspace (doc 08 §3.1
    ``workspaces[].role``); it is populated from the caller's membership and is
    ``None`` only when the workspace is returned outside a membership context.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    organization_id: UUID
    owner_id: UUID
    country_default: str
    role: MembershipRole | None = None
    created_at: datetime
    updated_at: datetime


class WorkspacePage(BaseModel):
    """A cursor-paginated page of workspaces (doc 06 §5, doc 08 §1.5).

    ``next_cursor`` is ``None`` on the last page; otherwise it is the opaque
    token the client passes back as ``?cursor=…`` to fetch the next page.
    """

    items: list[WorkspaceOut]
    next_cursor: str | None = None


# --- Members (B7) -----------------------------------------------------------


class MemberOut(BaseModel):
    """A workspace membership (doc 08 §2 ``/workspaces/{id}/members``)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    user_id: UUID
    role: MembershipRole
    invited_by: UUID | None = None
    invited_at: datetime | None = None
    joined_at: datetime | None = None
    created_at: datetime


class MemberPage(BaseModel):
    """A cursor-paginated page of workspace members (doc 06 §5, doc 08 §1.5)."""

    items: list[MemberOut]
    next_cursor: str | None = None


class MemberRoleUpdate(BaseModel):
    """Request body for ``PATCH /workspaces/{id}/members/{user_id}`` — set a role.

    ``owner`` is intentionally not assignable here: ownership is a single seat set
    at creation and transferred via a dedicated (later) flow, not by a generic
    role change. Admins manage ``admin``/``member``/``viewer``.
    """

    role: Literal[MembershipRole.ADMIN, MembershipRole.MEMBER, MembershipRole.VIEWER]
