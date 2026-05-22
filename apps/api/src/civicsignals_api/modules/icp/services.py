"""Public service interface for the icp module.

Other modules call icp only through the functions defined here — never by
importing icp's models or routes directly (doc 06 §3). This is the seam F2
(wizard) writes through, and that F3 (matcher/scorer) and F6 (backfill) read
through.

Every function is **workspace-scoped**: an ``IcpDefinition`` belongs to exactly
one workspace, so each read/write takes ``workspace_id`` and filters on it. A
caller can never reach another workspace's ICP through this surface — the basis
of tenant isolation (doc 07 §3).

Listing is **cursor**-paginated on the time-ordered UUID v7 id (doc 06 §5 —
keyset, never offset), the same shape as ``accounts.services``.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api import events

from .models import IcpDefinition

# Cursor pagination defaults (doc 06 §5, doc 08 §1.5).
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


class IcpError(Exception):
    """Base for icp-layer failures the routes translate into RFC 7807."""


class IcpNotFoundError(IcpError):
    """No ICP with the given id exists in the workspace."""


class IcpValidationError(IcpError):
    """An ICP field combination is invalid (e.g. an inverted size band).

    Pydantic catches most of this at the edge; this covers cross-field checks the
    service does against the *stored* row when a partial patch only supplies one
    bound of a range.
    """


@dataclass(slots=True)
class IcpPage:
    """A page of ICP definitions plus the next-page cursor (``None`` = last)."""

    items: list[IcpDefinition]
    next_cursor: str | None


def encode_cursor(icp_id: uuid.UUID) -> str:
    """Encode a keyset cursor (the last row's id) as an opaque base64 token."""
    return base64.urlsafe_b64encode(icp_id.bytes).decode("ascii")


def decode_cursor(cursor: str) -> uuid.UUID:
    """Decode a cursor token back to the icp id, or raise ``ValueError``."""
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


# Fields a create / update may set on the model. Centralized so create + update
# stay in sync and a stray attribute can't be written by accident.
_WRITABLE_FIELDS = (
    "name",
    "countries",
    "states",
    "entity_kinds",
    "signal_types",
    "min_size",
    "max_size",
    "signal_weights",
    "keywords_required",
    "keywords_excluded",
    "deal_band_min_cents",
    "deal_band_max_cents",
    "threshold",
)


def _validate_ranges(icp: IcpDefinition) -> None:
    """Reject an inverted size / deal band on the resolved (post-patch) row."""
    if icp.min_size is not None and icp.min_size < 0:
        raise IcpValidationError("min_size must be >= 0")
    if icp.max_size is not None and icp.max_size < 0:
        raise IcpValidationError("max_size must be >= 0")
    if icp.min_size is not None and icp.max_size is not None and icp.min_size > icp.max_size:
        raise IcpValidationError("min_size must be <= max_size")
    if icp.deal_band_min_cents is not None and icp.deal_band_min_cents < 0:
        raise IcpValidationError("deal_band_min_cents must be >= 0")
    if icp.deal_band_max_cents is not None and icp.deal_band_max_cents < 0:
        raise IcpValidationError("deal_band_max_cents must be >= 0")
    if (
        icp.deal_band_min_cents is not None
        and icp.deal_band_max_cents is not None
        and icp.deal_band_min_cents > icp.deal_band_max_cents
    ):
        raise IcpValidationError("deal_band_min_cents must be <= deal_band_max_cents")


async def _deactivate_others(
    session: AsyncSession, *, workspace_id: uuid.UUID, except_id: uuid.UUID | None
) -> None:
    """Set ``is_active = false`` on the workspace's other ICPs.

    Enforces the one-active-ICP-per-workspace invariant (doc 14 §3.1) *before* a
    row is flipped active, so the partial unique index never trips. ``except_id``
    is the row about to become / stay active.
    """
    stmt = (
        update(IcpDefinition)
        .where(
            IcpDefinition.workspace_id == workspace_id,
            IcpDefinition.is_active.is_(True),
        )
        # A bulk UPDATE bypasses the ORM ``onupdate=func.now()`` hook, so set
        # ``updated_at`` explicitly to keep the deactivated rows' timestamps in
        # step with the activation change.
        .values(is_active=False, updated_at=func.now())
    )
    if except_id is not None:
        stmt = stmt.where(IcpDefinition.id != except_id)
    await session.execute(stmt)
    await session.flush()


async def create_icp(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    countries: list[str] | None = None,
    states: list[str] | None = None,
    entity_kinds: list[str] | None = None,
    signal_types: list[str] | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    signal_weights: dict[str, Any] | None = None,
    keywords_required: list[str] | None = None,
    keywords_excluded: list[str] | None = None,
    deal_band_min_cents: int | None = None,
    deal_band_max_cents: int | None = None,
    threshold: int = 50,
    is_active: bool = True,
) -> IcpDefinition:
    """Create an ICP definition in ``workspace_id``. The caller commits.

    When ``is_active`` is true, any previously-active ICP in the workspace is
    deactivated first so the one-active-per-workspace invariant holds.
    """
    icp = IcpDefinition(
        workspace_id=workspace_id,
        name=name,
        countries=countries if countries is not None else ["US"],
        states=states or [],
        entity_kinds=entity_kinds or [],
        signal_types=signal_types or [],
        min_size=min_size,
        max_size=max_size,
        signal_weights=signal_weights or {},
        keywords_required=keywords_required or [],
        keywords_excluded=keywords_excluded or [],
        deal_band_min_cents=deal_band_min_cents,
        deal_band_max_cents=deal_band_max_cents,
        threshold=threshold,
        is_active=is_active,
    )
    _validate_ranges(icp)
    if is_active:
        await _deactivate_others(session, workspace_id=workspace_id, except_id=None)
    session.add(icp)
    await session.flush()
    # Eager-load server-managed columns (created_at/updated_at) now so the route
    # can serialize the row after committing without a lazy refresh that would
    # fail in async context (``expire_on_commit=False`` keeps the loaded state).
    await session.refresh(icp)
    # F6: notify the backfill listener that this workspace's ICP changed so it can
    # re-score candidate signals. Only when active — an inactive ICP has no feed lens.
    if icp.is_active:
        await events.publish(
            events.ICP_CHANGED,
            {"workspace_id": str(icp.workspace_id), "icp_id": str(icp.id)},
        )
    return icp


async def get_icp(
    session: AsyncSession, *, workspace_id: uuid.UUID, icp_id: uuid.UUID
) -> IcpDefinition | None:
    """Fetch one ICP by id, scoped to the workspace, or ``None``."""
    result = await session.execute(
        select(IcpDefinition).where(
            IcpDefinition.id == icp_id,
            IcpDefinition.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def get_active_icp(session: AsyncSession, *, workspace_id: uuid.UUID) -> IcpDefinition | None:
    """Return the workspace's single active ICP, or ``None``.

    The matcher (F3) and backfill (F6) use this to load the definition they score
    a signal against (doc 14 §6).
    """
    result = await session.execute(
        select(IcpDefinition).where(
            IcpDefinition.workspace_id == workspace_id,
            IcpDefinition.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def list_icps(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> IcpPage:
    """Cursor-paginated list of the workspace's ICP definitions.

    Ordered by id (UUID v7, time-ordered) so the keyset cursor gives a stable
    total order. Fetches ``limit + 1`` rows to decide whether a next page exists.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(IcpDefinition).where(IcpDefinition.workspace_id == workspace_id)
    if cursor is not None:
        stmt = stmt.where(IcpDefinition.id > decode_cursor(cursor))
    stmt = stmt.order_by(IcpDefinition.id).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return IcpPage(items=items, next_cursor=next_cursor)


async def update_icp(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    icp_id: uuid.UUID,
    changes: dict[str, Any],
) -> IcpDefinition:
    """Patch an ICP's fields (partial update). The caller commits.

    ``changes`` carries only the supplied fields (``None``-valued / absent fields
    are not in the dict). Activation is **not** changed here — that goes through
    :func:`set_active`. Raises :class:`IcpNotFoundError` if the ICP is not in the
    workspace, :class:`IcpValidationError` on an inverted band.
    """
    icp = await get_icp(session, workspace_id=workspace_id, icp_id=icp_id)
    if icp is None:
        raise IcpNotFoundError("no such ICP in this workspace")
    for field, value in changes.items():
        if field not in _WRITABLE_FIELDS:
            # Defensive: routes only pass schema fields, but never let an
            # unexpected key (e.g. workspace_id, is_active) be written here.
            continue
        setattr(icp, field, value)
    _validate_ranges(icp)
    await session.flush()
    # Reload server-managed columns (the ``updated_at`` onupdate trigger) so the
    # route can serialize the row post-commit without an async lazy refresh.
    await session.refresh(icp)
    # F6: a patch to the active ICP's scoring criteria means the workspace feed
    # may be stale — kick the backfill so re-scoring happens promptly.
    if icp.is_active:
        await events.publish(
            events.ICP_CHANGED,
            {"workspace_id": str(icp.workspace_id), "icp_id": str(icp.id)},
        )
    return icp


async def set_active(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    icp_id: uuid.UUID,
    is_active: bool = True,
) -> IcpDefinition:
    """Activate (or deactivate) an ICP, enforcing one-active-per-workspace.

    Activating an ICP deactivates the workspace's other ICPs first (doc 14 §3.1)
    so the partial unique index never trips. The caller commits. Raises
    :class:`IcpNotFoundError` if the ICP is not in the workspace.
    """
    icp = await get_icp(session, workspace_id=workspace_id, icp_id=icp_id)
    if icp is None:
        raise IcpNotFoundError("no such ICP in this workspace")
    if is_active:
        await _deactivate_others(session, workspace_id=workspace_id, except_id=icp.id)
    icp.is_active = is_active
    await session.flush()
    # Reload server-managed columns (the ``updated_at`` onupdate trigger) so the
    # route can serialize the row post-commit without an async lazy refresh.
    await session.refresh(icp)
    # F6: activating a (different) ICP lens changes the workspace's feed — trigger
    # a backfill so the new ICP's scores are computed promptly. A *de*activation
    # produces no feed (no active ICP), so no backfill needed.
    if icp.is_active:
        await events.publish(
            events.ICP_CHANGED,
            {"workspace_id": str(icp.workspace_id), "icp_id": str(icp.id)},
        )
    return icp
