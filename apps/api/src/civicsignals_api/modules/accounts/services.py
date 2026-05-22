"""Public service interface for the accounts module.

Other modules call accounts only through the functions defined here — never by
importing accounts's models or routes directly (doc 06 §3). This is the seam the
``auth`` module uses to create/look up users, and that B5 (workspaces) extends
with the workspace + membership surface every workspace-scoped module scopes
through (B6 invites, B7 RBAC, B9 audit, F1 ICP, J1 pipeline, N1 billing, C3
directory). B7 (RBAC) layers role enforcement on top of these.
"""

from __future__ import annotations

import base64
import binascii
import re
import secrets
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Membership, MembershipRole, Organization, User, Workspace

# Cursor pagination defaults (doc 06 §5, doc 08 §1.5). Hard cap keeps an
# unbounded ``limit`` from scanning every workspace a user belongs to.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


async def get_user_by_id(session: AsyncSession, user_id: UUID) -> User | None:
    """Return the (non-deleted) user with this id, or ``None``."""
    result = await session.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Return the (non-deleted) user with this email (case-insensitive), or ``None``."""
    result = await session.execute(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    password_hash: str | None = None,
    name: str | None = None,
    email_verified: bool = False,
) -> User:
    """Insert a new user. The caller is responsible for hashing the password
    (via ``auth.services.hash_password``) and for handling uniqueness conflicts.
    """
    user = User(
        email=email,
        password_hash=password_hash,
        name=name,
        email_verified_at=datetime.now(UTC) if email_verified else None,
    )
    session.add(user)
    await session.flush()
    return user


async def mark_email_verified(session: AsyncSession, user: User) -> User:
    """Set the user's ``email_verified_at`` to now (idempotent)."""
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        await session.flush()
    return user


async def touch_last_seen(session: AsyncSession, user: User) -> None:
    """Update ``last_seen_at`` to now (called on login / authenticated access)."""
    user.last_seen_at = datetime.now(UTC)
    await session.flush()


# --- Workspaces (B5) --------------------------------------------------------
# A workspace is the tenant boundary (doc 07 §1). Every workspace-scoped module
# resolves the active workspace through ``get_membership`` (the seam the auth
# dependency uses for ``X-Workspace-Id``). Listing is **cursor**-paginated on the
# time-ordered UUID v7 id (doc 06 §5 — keyset, never offset).


class WorkspaceError(Exception):
    """Base for workspace-layer failures the routes translate into RFC 7807."""


class SlugConflictError(WorkspaceError):
    """A workspace slug collided after exhausting the disambiguation suffixes."""


@dataclass(slots=True)
class WorkspacePage:
    """A page of workspaces plus the cursor for the next page (``None`` = last)."""

    items: list[Workspace]
    next_cursor: str | None


def encode_cursor(row_id: uuid.UUID) -> str:
    """Encode a keyset cursor (the last row's UUID v7 id) as an opaque base64 token.

    Id-agnostic: used for both the workspace list (workspace id) and the member
    list (membership id) — any keyset paginated on a UUID v7 primary key.
    """
    return base64.urlsafe_b64encode(row_id.bytes).decode("ascii")


def decode_cursor(cursor: str) -> uuid.UUID:
    """Decode a cursor token back to the row id, or raise ``ValueError``.

    Counterpart to :func:`encode_cursor`; returns the opaque token's UUID v7 id
    regardless of which table it paginates.
    """
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:  # malformed token
        raise ValueError("invalid cursor") from exc


_SLUG_CLEAN = re.compile(r"[^a-z0-9]+")
_SLUG_TRIM = re.compile(r"(^-+|-+$)")

# Slug column / schema cap (``WorkspaceCreate.slug`` max_length). The bare stem
# is truncated to this; a disambiguation suffix (``-rand4``, 5 chars) reserves
# room so the final slug never exceeds the cap.
SLUG_MAX_LEN = 48
_SLUG_SUFFIX_LEN = 5


def slugify(name: str) -> str:
    """Turn a workspace name into a URL-safe slug stem (ASCII, lowercase, dashed).

    Falls back to ``"workspace"`` when the name has no slug-able characters
    (e.g. all emoji); :func:`_unique_slug` then appends a random suffix.
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_CLEAN.sub("-", ascii_name.lower())
    slug = _SLUG_TRIM.sub("", slug)
    return slug[:SLUG_MAX_LEN] or "workspace"


async def _slug_exists(session: AsyncSession, slug: str) -> bool:
    result = await session.execute(select(Workspace.id).where(Workspace.slug == slug))
    return result.first() is not None


async def _unique_slug(session: AsyncSession, name: str) -> str:
    """Return a globally-unique slug derived from ``name``.

    Tries the bare stem first, then ``<stem>-<rand4>`` a handful of times. The
    stem is re-truncated to leave room for the suffix so the final slug stays
    within :data:`SLUG_MAX_LEN`. Slug uniqueness is also enforced by a DB
    constraint, so a lost race surfaces as an ``IntegrityError`` the route maps
    to ``409`` — this just avoids the common case.
    """
    stem = slugify(name)
    if not await _slug_exists(session, stem):
        return stem
    # Leave room for the "-rand4" suffix so the combined slug fits the cap.
    suffix_stem = stem[: SLUG_MAX_LEN - _SLUG_SUFFIX_LEN].rstrip("-") or "workspace"
    for _ in range(8):
        candidate = f"{suffix_stem}-{secrets.token_hex(2)}"
        if not await _slug_exists(session, candidate):
            return candidate
    raise SlugConflictError("could not allocate a unique workspace slug")


async def create_workspace(
    session: AsyncSession,
    *,
    owner: User,
    name: str,
    slug: str | None = None,
    country_default: str = "US",
) -> Workspace:
    """Create a workspace owned by ``owner``, with the owner as an ``owner`` member.

    A dedicated organization is created to satisfy the
    ``accounts_workspace.organization_id`` FK (doc 07); billing (N1) reuses it.
    The creator's membership row is added with role ``owner`` and ``joined_at``
    set to now. The caller commits.
    """
    organization = Organization(name=name, billing_email=owner.email)
    session.add(organization)
    await session.flush()

    resolved_slug = slug or await _unique_slug(session, name)
    workspace = Workspace(
        organization_id=organization.id,
        name=name,
        slug=resolved_slug,
        owner_id=owner.id,
        country_default=country_default.upper(),
    )
    session.add(workspace)
    await session.flush()

    now = datetime.now(UTC)
    membership = Membership(
        workspace_id=workspace.id,
        user_id=owner.id,
        role=MembershipRole.OWNER,
        joined_at=now,
    )
    session.add(membership)
    await session.flush()
    return workspace


async def get_workspace(session: AsyncSession, workspace_id: uuid.UUID) -> Workspace | None:
    """Fetch one non-deleted workspace by id, or ``None``."""
    result = await session.execute(
        select(Workspace).where(Workspace.id == workspace_id, Workspace.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


async def get_membership(
    session: AsyncSession, *, workspace_id: uuid.UUID, user_id: uuid.UUID
) -> Membership | None:
    """Return the user's membership in the (non-deleted) workspace, or ``None``.

    This is the seam the auth dependency (``require_workspace``) uses to validate
    ``X-Workspace-Id`` against the caller's membership set (doc 08 §1.4) and that
    B7 reads the role from for RBAC.
    """
    result = await session.execute(
        select(Membership)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(
            Membership.workspace_id == workspace_id,
            Membership.user_id == user_id,
            Workspace.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


def _user_workspaces_query(user_id: uuid.UUID) -> Select[tuple[Workspace]]:
    return (
        select(Workspace)
        .join(Membership, Membership.workspace_id == Workspace.id)
        .where(Membership.user_id == user_id, Workspace.deleted_at.is_(None))
    )


async def list_workspaces_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> WorkspacePage:
    """Cursor-paginated list of the workspaces ``user_id`` is a member of.

    Ordered by id (UUID v7, time-ordered) so the keyset cursor gives a stable
    total order. Fetches ``limit + 1`` rows to decide whether a next page exists.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = _user_workspaces_query(user_id)
    if cursor is not None:
        stmt = stmt.where(Workspace.id > decode_cursor(cursor))
    stmt = stmt.order_by(Workspace.id).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return WorkspacePage(items=items, next_cursor=next_cursor)


async def list_roles_for_user(
    session: AsyncSession, user_id: uuid.UUID, workspace_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, MembershipRole]:
    """Map ``workspace_id -> role`` for the given workspaces (for response shaping)."""
    if not workspace_ids:
        return {}
    result = await session.execute(
        select(Membership.workspace_id, Membership.role).where(
            Membership.user_id == user_id,
            Membership.workspace_id.in_(list(workspace_ids)),
        )
    )
    return {row[0]: row[1] for row in result.all()}


async def set_last_active_workspace(
    session: AsyncSession, *, user: User, workspace_id: uuid.UUID
) -> User:
    """Set the user's ``last_active_workspace_id`` (the ``/switch`` action).

    The caller is responsible for having verified membership first (the route /
    dependency does). The caller commits.
    """
    user.last_active_workspace_id = workspace_id
    await session.flush()
    return user


# --- Membership management (B7 RBAC; B6 invitations build on this) ----------
# Adding members and changing roles are *admin*-gated actions (doc 06 §6); the
# routes enforce that via ``require_role`` and these services do the writes. B6
# (invitations) reuses ``add_member`` to materialize an accepted invite.


async def add_member(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: MembershipRole = MembershipRole.MEMBER,
    invited_by: uuid.UUID | None = None,
) -> Membership:
    """Add ``user_id`` to ``workspace_id`` with ``role`` (default ``member``).

    ``joined_at`` is stamped now (direct add); B6 sets ``invited_at`` separately
    on the invite path. Uniqueness on ``(workspace_id, user_id)`` is enforced by a
    DB constraint, so a duplicate surfaces as an ``IntegrityError`` for the route
    to map to ``409``. The caller commits.
    """
    membership = Membership(
        workspace_id=workspace_id,
        user_id=user_id,
        role=role,
        invited_by=invited_by,
        joined_at=datetime.now(UTC),
    )
    session.add(membership)
    await session.flush()
    return membership


async def set_member_role(
    session: AsyncSession, *, membership: Membership, role: MembershipRole
) -> Membership:
    """Update an existing membership's ``role`` (an admin action). The caller commits."""
    membership.role = role
    await session.flush()
    return membership


@dataclass(slots=True)
class MembershipPage:
    """A page of memberships plus the cursor for the next page (``None`` = last)."""

    items: list[Membership]
    next_cursor: str | None


async def list_members(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> MembershipPage:
    """Cursor-paginated list of memberships in ``workspace_id`` (keyset on id).

    Ordered by the time-ordered UUID v7 id so the cursor gives a stable total
    order (doc 06 §5). Fetches ``limit + 1`` rows to decide ``has_more``.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = (
        select(Membership)
        .join(Workspace, Workspace.id == Membership.workspace_id)
        .where(Membership.workspace_id == workspace_id, Workspace.deleted_at.is_(None))
    )
    if cursor is not None:
        stmt = stmt.where(Membership.id > decode_cursor(cursor))
    stmt = stmt.order_by(Membership.id).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return MembershipPage(items=items, next_cursor=next_cursor)
