"""Public service interface for the searches module.

Other modules call searches only through the functions defined here — never by
importing searches's models or routes directly (doc 06 §3). This is the seam the
H1 management UI writes through, and the seam the digest scheduler (H3) will read
through to enumerate due searches.

Every function is **workspace-scoped**: a :class:`SavedSearch` belongs to exactly
one workspace, so each read/write takes ``workspace_id`` and filters on it — a
caller can never reach another workspace's searches (doc 07 §3, the basis of
tenant isolation).

Within a workspace, **ownership** gates writes (H1 sharing model):

- ``create`` records the calling user as ``created_by``.
- ``list`` returns the caller's own searches plus every *shared* search in the
  workspace (so a shared search is visible to all members).
- ``get`` resolves a search the caller can *see* — their own, or a shared one.
- ``update`` / ``delete`` require the caller to be the **owner**; a non-owner
  editing/deleting another member's (even shared) search raises
  :class:`SavedSearchForbiddenError`, which the route maps to ``403``.

Listing is **cursor**-paginated on the time-ordered UUID v7 id (doc 06 §5 —
keyset, never offset), the same shape as ``icp.services``.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SavedSearch

# Cursor pagination defaults (doc 06 §5, doc 08 §1.5).
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


class SavedSearchError(Exception):
    """Base for searches-layer failures the routes translate into RFC 7807."""


class SavedSearchNotFoundError(SavedSearchError):
    """No saved search the caller may see exists for the given id/workspace."""


class SavedSearchForbiddenError(SavedSearchError):
    """The caller may see the search but is not its owner, so cannot mutate it."""


@dataclass(slots=True)
class SavedSearchPage:
    """A page of saved searches plus the next-page cursor (``None`` = last)."""

    items: list[SavedSearch]
    next_cursor: str | None


def encode_cursor(search_id: uuid.UUID) -> str:
    """Encode a keyset cursor (the last row's id) as an opaque base64 token."""
    return base64.urlsafe_b64encode(search_id.bytes).decode("ascii")


def decode_cursor(cursor: str) -> uuid.UUID:
    """Decode a cursor token back to the search id, or raise ``ValueError``."""
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


async def create_saved_search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    filters: dict[str, Any] | None = None,
    is_shared: bool = False,
) -> SavedSearch:
    """Create a saved search owned by ``created_by`` in ``workspace_id``.

    ``filters`` is the already-validated G1 feed filter blob (the route validates
    it through :class:`~searches.schemas.SearchFilters` before calling). The
    caller commits.
    """
    search = SavedSearch(
        workspace_id=workspace_id,
        created_by=created_by,
        name=name,
        filters=filters or {},
        is_shared=is_shared,
    )
    session.add(search)
    await session.flush()
    # Eager-load server-managed columns (created_at/updated_at) so the route can
    # serialize the row after commit without an async lazy refresh.
    await session.refresh(search)
    return search


async def _get_visible(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    search_id: uuid.UUID,
) -> SavedSearch | None:
    """Fetch a search the caller may *see*: their own, or a shared one.

    Returns ``None`` when no such search exists in the workspace, or when it
    exists but is private to another member (existence is not leaked — the route
    maps ``None`` to ``404``).
    """
    result = await session.execute(
        select(SavedSearch).where(
            SavedSearch.id == search_id,
            SavedSearch.workspace_id == workspace_id,
            or_(SavedSearch.created_by == user_id, SavedSearch.is_shared.is_(True)),
        )
    )
    return result.scalar_one_or_none()


async def get_saved_search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    search_id: uuid.UUID,
) -> SavedSearch | None:
    """Fetch one saved search the caller may see (own or shared), or ``None``."""
    return await _get_visible(
        session, workspace_id=workspace_id, user_id=user_id, search_id=search_id
    )


async def list_saved_searches(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> SavedSearchPage:
    """Cursor-paginated list of searches the caller may see in the workspace.

    Includes the caller's own searches plus every shared search in the workspace
    (the H1 "share within workspace" rule). Ordered by id (UUID v7, time-ordered)
    so the keyset cursor gives a stable total order; fetches ``limit + 1`` rows to
    decide whether a next page exists.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(SavedSearch).where(
        SavedSearch.workspace_id == workspace_id,
        or_(SavedSearch.created_by == user_id, SavedSearch.is_shared.is_(True)),
    )
    if cursor is not None:
        stmt = stmt.where(SavedSearch.id > decode_cursor(cursor))
    stmt = stmt.order_by(SavedSearch.id).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return SavedSearchPage(items=items, next_cursor=next_cursor)


async def _get_owned(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    search_id: uuid.UUID,
) -> SavedSearch:
    """Resolve a search the caller may mutate, or raise.

    Distinguishes the two failure modes the routes map to distinct statuses:
    a search the caller cannot even *see* is :class:`SavedSearchNotFoundError`
    (``404`` — existence not leaked); a *visible but not-owned* one is
    :class:`SavedSearchForbiddenError` (``403`` — it exists, you just can't edit
    it).
    """
    visible = await _get_visible(
        session, workspace_id=workspace_id, user_id=user_id, search_id=search_id
    )
    if visible is None:
        raise SavedSearchNotFoundError("no such saved search in this workspace")
    if visible.created_by != user_id:
        raise SavedSearchForbiddenError("only the owner may modify this saved search")
    return visible


async def update_saved_search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    search_id: uuid.UUID,
    name: str | None = None,
    filters: dict[str, Any] | None = None,
    is_shared: bool | None = None,
) -> SavedSearch:
    """Patch a saved search the caller owns (rename / re-filter / share toggle).

    Only the supplied (non-``None``) fields change. ``filters`` replaces the whole
    filter blob (already validated by the route). The caller commits. Raises
    :class:`SavedSearchNotFoundError` / :class:`SavedSearchForbiddenError`.
    """
    search = await _get_owned(
        session, workspace_id=workspace_id, user_id=user_id, search_id=search_id
    )
    if name is not None:
        search.name = name
    if filters is not None:
        search.filters = filters
    if is_shared is not None:
        search.is_shared = is_shared
    await session.flush()
    # Reload server-managed columns (the ``updated_at`` onupdate trigger) so the
    # route can serialize the row post-commit without an async lazy refresh.
    await session.refresh(search)
    return search


async def delete_saved_search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    search_id: uuid.UUID,
) -> None:
    """Delete a saved search the caller owns. The caller commits.

    Raises :class:`SavedSearchNotFoundError` (cannot see it) or
    :class:`SavedSearchForbiddenError` (visible but not owner).

    # TODO H3: when a digest schedule is attached to a saved search, deletion must
    #   also cancel/clean up the schedule row so the scheduler stops dispatching.
    """
    search = await _get_owned(
        session, workspace_id=workspace_id, user_id=user_id, search_id=search_id
    )
    await session.delete(search)
    await session.flush()
