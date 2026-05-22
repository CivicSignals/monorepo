"""Public service interface for the pipeline module (J1).

Other modules call pipeline only through the functions defined here — never by
importing pipeline's models or routes directly (doc 06 §3).

This is the seam J2 (Kanban UI), J3 (activity log), J4 (manual items), and
J5 (reporting) depend on.

Cursor pagination uses the same keyset strategy as the accounts module (doc 06
§5): encode the last row's UUID v7 id as an opaque base64 token; decode on the
next request and filter ``id > cursor_id``.

All functions are workspace-scoped: callers pass ``workspace_id`` and the
service NEVER queries across workspace boundaries.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.ids import uuid7

from .models import DEFAULT_STAGES, ItemStatus, PipelineItem, PipelineStage

# Pagination defaults (doc 06 §5, doc 08 §1.5).
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

# Sentinel for distinguishing "field omitted" from "field explicitly set to None"
# in update payloads (used by update_item).
UNSET: object = object()


# ---------------------------------------------------------------------------
# Cursor helpers (shared between stages and items)
# ---------------------------------------------------------------------------


def encode_cursor(row_id: uuid.UUID) -> str:
    """Encode a UUID keyset cursor as an opaque URL-safe base64 token."""
    return base64.urlsafe_b64encode(row_id.bytes).decode("ascii")


def decode_cursor(cursor: str) -> uuid.UUID:
    """Decode a cursor token back to a UUID, or raise ``ValueError``."""
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Base for pipeline-layer failures the routes translate into RFC 7807."""


class StageNotFoundError(PipelineError):
    """No such stage in this workspace."""


class ItemNotFoundError(PipelineError):
    """No such item in this workspace."""


class StageNameConflictError(PipelineError):
    """A stage with this name already exists in the workspace."""


class StageInUseError(PipelineError):
    """Cannot delete a stage that has items."""


class ReorderError(PipelineError):
    """Reorder payload does not match the workspace's stage set."""


# ---------------------------------------------------------------------------
# Stage data class
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StagePage:
    """A page of stages with the next-page cursor."""

    items: list[PipelineStage]
    next_cursor: str | None


@dataclass(slots=True)
class ItemPage:
    """A page of items with the next-page cursor."""

    items: list[PipelineItem]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# Stage provisioning
# ---------------------------------------------------------------------------


async def provision_default_stages(session: AsyncSession, *, workspace_id: uuid.UUID) -> None:
    """Seed the nine default Kanban stages for a workspace (PRD F14.1, J1).

    Idempotent and concurrency-safe: uses INSERT … ON CONFLICT DO NOTHING so
    concurrent first-use requests converge without raising IntegrityError.
    Called lazily on first stage-list or item-create request when the workspace
    has no stages yet. The caller commits.

    # TODO J2: consider calling this from workspace-creation (B5) so all
    # workspaces have stages from day one rather than lazily.
    """
    existing = await session.execute(
        select(PipelineStage.id).where(PipelineStage.workspace_id == workspace_id).limit(1)
    )
    if existing.first() is not None:
        return  # already provisioned — fast path avoids pg_insert overhead

    # Use INSERT … ON CONFLICT DO NOTHING so concurrent first-use requests don't
    # race into a UNIQUE violation on (workspace_id, name).
    for name, position, is_default in DEFAULT_STAGES:
        stmt = (
            pg_insert(PipelineStage)
            .values(
                id=uuid7(),  # Python-side default; pg_insert bypasses ORM defaults
                workspace_id=workspace_id,
                name=name,
                position=position,
                is_default=is_default,
            )
            .on_conflict_do_nothing(constraint="uq_pipeline_stage_ws_name")
        )
        await session.execute(stmt)
    await session.flush()


# ---------------------------------------------------------------------------
# Stage CRUD
# ---------------------------------------------------------------------------


async def list_stages(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> StagePage:
    """Cursor-paginated list of stages, ordered by position then id.

    Auto-provisions default stages if the workspace has none yet.
    """
    limit = max(1, min(limit, MAX_LIMIT))

    # Lazy provisioning — first list call for a new workspace seeds the defaults.
    await provision_default_stages(session, workspace_id=workspace_id)

    stmt = select(PipelineStage).where(PipelineStage.workspace_id == workspace_id)
    if cursor is not None:
        cursor_id = decode_cursor(cursor)
        # Keyset on (position, id) — stable even when positions are equal.
        cursor_row = await _get_stage(session, workspace_id, cursor_id)
        if cursor_row is None:
            raise ValueError("invalid cursor")
        stmt = stmt.where(
            (PipelineStage.position > cursor_row.position)
            | ((PipelineStage.position == cursor_row.position) & (PipelineStage.id > cursor_id))
        )
    stmt = stmt.order_by(PipelineStage.position, PipelineStage.id).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return StagePage(items=items, next_cursor=next_cursor)


async def _get_stage(
    session: AsyncSession, workspace_id: uuid.UUID, stage_id: uuid.UUID
) -> PipelineStage | None:
    result = await session.execute(
        select(PipelineStage).where(
            PipelineStage.id == stage_id,
            PipelineStage.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def get_stage(
    session: AsyncSession, workspace_id: uuid.UUID, stage_id: uuid.UUID
) -> PipelineStage:
    """Return a stage or raise :class:`StageNotFoundError`."""
    stage = await _get_stage(session, workspace_id, stage_id)
    if stage is None:
        raise StageNotFoundError(stage_id)
    return stage


async def _name_exists(session: AsyncSession, workspace_id: uuid.UUID, name: str) -> bool:
    result = await session.execute(
        select(PipelineStage.id).where(
            PipelineStage.workspace_id == workspace_id,
            PipelineStage.name == name,
        )
    )
    return result.first() is not None


async def _max_position(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    result = await session.execute(
        select(PipelineStage.position)
        .where(PipelineStage.workspace_id == workspace_id)
        .order_by(PipelineStage.position.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    return row if row is not None else -1


async def create_stage(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    name: str,
    is_default: bool = False,
) -> PipelineStage:
    """Append a new stage at the end of the workspace's stage list.

    Raises :class:`StageNameConflictError` on duplicate name. If ``is_default``
    is True, clears the flag on any existing default stage first (only one
    default per workspace). The caller commits.
    """
    await provision_default_stages(session, workspace_id=workspace_id)
    if await _name_exists(session, workspace_id, name):
        raise StageNameConflictError(name)
    if is_default:
        await _clear_default(session, workspace_id)
    position = (await _max_position(session, workspace_id)) + 1
    stage = PipelineStage(
        workspace_id=workspace_id,
        name=name,
        position=position,
        is_default=is_default,
    )
    session.add(stage)
    await session.flush()
    return stage


async def _clear_default(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Clear is_default on all stages in this workspace."""
    await session.execute(
        update(PipelineStage)
        .where(PipelineStage.workspace_id == workspace_id, PipelineStage.is_default.is_(True))
        .values(is_default=False)
    )


async def update_stage(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    stage_id: uuid.UUID,
    *,
    name: str | None = None,
    is_default: bool | None = None,
) -> PipelineStage:
    """Update stage name and/or default flag.

    Raises :class:`StageNotFoundError` or :class:`StageNameConflictError`.
    The caller commits.
    """
    stage = await get_stage(session, workspace_id, stage_id)
    if name is not None and name != stage.name:
        if await _name_exists(session, workspace_id, name):
            raise StageNameConflictError(name)
        stage.name = name
    if is_default is not None:
        if is_default and not stage.is_default:
            await _clear_default(session, workspace_id)
        stage.is_default = is_default
    await session.flush()
    await session.refresh(stage)
    return stage


async def delete_stage(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    stage_id: uuid.UUID,
) -> None:
    """Delete a stage (only if it has no items).

    Raises :class:`StageNotFoundError` or :class:`StageInUseError`. The caller
    commits.
    """
    stage = await get_stage(session, workspace_id, stage_id)
    # Check for items
    has_items = await session.execute(
        select(PipelineItem.id)
        .where(
            PipelineItem.stage_id == stage_id,
            PipelineItem.workspace_id == workspace_id,
        )
        .limit(1)
    )
    if has_items.first() is not None:
        raise StageInUseError(stage_id)
    await session.delete(stage)
    # Flush the delete first so the stage row is gone from the session identity
    # map before _recompact_positions queries remaining stages — otherwise the
    # deleted row can still appear in the SELECT and produce gaps.
    await session.flush()
    # Re-compact positions so there are no gaps after deletion.
    await _recompact_positions(session, workspace_id)


async def _recompact_positions(session: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Re-assign 0-based positions in order of current position after a deletion."""
    result = await session.execute(
        select(PipelineStage)
        .where(PipelineStage.workspace_id == workspace_id)
        .order_by(PipelineStage.position, PipelineStage.id)
    )
    stages = list(result.scalars().all())
    for idx, stage in enumerate(stages):
        if stage.position != idx:
            stage.position = idx


async def reorder_stages(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    ordered_ids: list[uuid.UUID],
) -> list[PipelineStage]:
    """Reorder stages so their positions match the supplied id order (0-based).

    ``ordered_ids`` must contain exactly the same ids as the workspace's current
    stage set. Raises :class:`ReorderError` on mismatch. The caller commits.
    """
    result = await session.execute(
        select(PipelineStage).where(PipelineStage.workspace_id == workspace_id)
    )
    existing = {s.id: s for s in result.scalars().all()}

    supplied = set(ordered_ids)
    if supplied != set(existing.keys()):
        raise ReorderError(
            "Reorder list must contain exactly the workspace's stage ids — "
            f"missing: {set(existing.keys()) - supplied}, "
            f"extra: {supplied - set(existing.keys())}"
        )
    if len(ordered_ids) != len(supplied):
        raise ReorderError("Duplicate stage id in reorder list.")

    for new_pos, stage_id in enumerate(ordered_ids):
        existing[stage_id].position = new_pos
    await session.flush()
    for stage in existing.values():
        await session.refresh(stage)
    return [existing[sid] for sid in ordered_ids]


# ---------------------------------------------------------------------------
# Item CRUD
# ---------------------------------------------------------------------------


async def _default_stage_id(session: AsyncSession, workspace_id: uuid.UUID) -> uuid.UUID:
    """Return the default stage id for the workspace, or the first stage id."""
    result = await session.execute(
        select(PipelineStage)
        .where(PipelineStage.workspace_id == workspace_id, PipelineStage.is_default.is_(True))
        .limit(1)
    )
    default = result.scalar_one_or_none()
    if default is not None:
        return default.id
    # Fallback: pick the lowest-position stage.
    result2 = await session.execute(
        select(PipelineStage)
        .where(PipelineStage.workspace_id == workspace_id)
        .order_by(PipelineStage.position)
        .limit(1)
    )
    fallback = result2.scalar_one_or_none()
    if fallback is None:
        raise PipelineError("Workspace has no stages; cannot create an item.")
    return fallback.id


async def create_item(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    title: str,
    stage_id: uuid.UUID | None = None,
    signal_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    notes: str | None = None,
    value_estimate: Decimal | None = None,
) -> PipelineItem:
    """Create a pipeline item in the workspace.

    If ``stage_id`` is omitted the workspace's default stage is used (lazy-
    provisioning default stages if needed). The caller commits.
    """
    await provision_default_stages(session, workspace_id=workspace_id)
    resolved_stage_id = stage_id or await _default_stage_id(session, workspace_id)
    # Verify the stage belongs to this workspace.
    await get_stage(session, workspace_id, resolved_stage_id)
    item = PipelineItem(
        workspace_id=workspace_id,
        stage_id=resolved_stage_id,
        signal_id=signal_id,
        owner_id=owner_id,
        title=title,
        notes=notes,
        value_estimate=value_estimate,
        status=ItemStatus.ACTIVE,
    )
    session.add(item)
    await session.flush()
    return item


async def _get_item(
    session: AsyncSession, workspace_id: uuid.UUID, item_id: uuid.UUID
) -> PipelineItem | None:
    result = await session.execute(
        select(PipelineItem).where(
            PipelineItem.id == item_id,
            PipelineItem.workspace_id == workspace_id,
        )
    )
    return result.scalar_one_or_none()


async def get_item(
    session: AsyncSession, workspace_id: uuid.UUID, item_id: uuid.UUID
) -> PipelineItem:
    """Return an item or raise :class:`ItemNotFoundError`."""
    item = await _get_item(session, workspace_id, item_id)
    if item is None:
        raise ItemNotFoundError(item_id)
    return item


async def list_items(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    stage_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    status: ItemStatus | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> ItemPage:
    """Cursor-paginated list of items, ordered by creation time (id).

    Optional filters: ``stage_id``, ``owner_id``, ``status``.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(PipelineItem).where(PipelineItem.workspace_id == workspace_id)
    if stage_id is not None:
        stmt = stmt.where(PipelineItem.stage_id == stage_id)
    if owner_id is not None:
        stmt = stmt.where(PipelineItem.owner_id == owner_id)
    if status is not None:
        stmt = stmt.where(PipelineItem.status == status)
    if cursor is not None:
        cursor_id = decode_cursor(cursor)
        stmt = stmt.where(PipelineItem.id > cursor_id)
    stmt = stmt.order_by(PipelineItem.id).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return ItemPage(items=items, next_cursor=next_cursor)


async def update_item(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    title: str | None = None,
    notes: object = UNSET,
    value_estimate: object = UNSET,
    status: ItemStatus | None = None,
    owner_id: object = UNSET,
) -> PipelineItem:
    """Partial update of a pipeline item.

    Fields use ``UNSET`` as a sentinel to distinguish "not provided" from
    "explicitly set to null". Passing ``notes=None``, ``value_estimate=None``,
    or ``owner_id=None`` clears those nullable fields. The caller commits.
    """
    item = await get_item(session, workspace_id, item_id)
    if title is not None:
        item.title = title
    if notes is not UNSET:
        item.notes = None if notes is None else str(notes)
    if value_estimate is not UNSET:
        item.value_estimate = None if value_estimate is None else Decimal(str(value_estimate))
    if status is not None:
        item.status = status
    if owner_id is not UNSET:
        item.owner_id = None if owner_id is None else uuid.UUID(str(owner_id))
    await session.flush()
    await session.refresh(item)
    return item


async def move_item(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    stage_id: uuid.UUID,
    status: ItemStatus | None = None,
) -> PipelineItem:
    """Move an item to a different stage (J2 Kanban DnD seam).

    Verifies the target stage belongs to the same workspace. Optionally updates
    status at the same time (e.g. dragging to Won → status=won). The caller
    commits.
    """
    item = await get_item(session, workspace_id, item_id)
    await get_stage(session, workspace_id, stage_id)  # workspace check
    item.stage_id = stage_id
    if status is not None:
        item.status = status
    await session.flush()
    await session.refresh(item)
    return item


async def assign_owner(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    owner_id: uuid.UUID | None,
) -> PipelineItem:
    """Set (or clear) the assignee on a pipeline item. The caller commits."""
    item = await get_item(session, workspace_id, item_id)
    item.owner_id = owner_id
    await session.flush()
    await session.refresh(item)
    return item


async def set_value(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    *,
    value_estimate: Decimal | None,
) -> PipelineItem:
    """Set or clear the value estimate on an item. The caller commits."""
    item = await get_item(session, workspace_id, item_id)
    item.value_estimate = value_estimate
    await session.flush()
    return item


async def delete_item(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
) -> None:
    """Hard-delete a pipeline item. The caller commits."""
    item = await get_item(session, workspace_id, item_id)
    await session.delete(item)
    await session.flush()


# ---------------------------------------------------------------------------
# Reporting (J5)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StageRollupRow:
    """One row in the per-stage rollup produced by :func:`rollup_by_stage`.

    ``item_count`` — total items in this stage.
    ``total_value`` — sum of non-null value_estimate; ``None`` when no values.
    """

    stage_id: uuid.UUID
    stage_name: str
    stage_position: int
    item_count: int
    total_value: Decimal | None


@dataclass(slots=True)
class PipelineRollup:
    """Workspace-level rollup returned by :func:`rollup_by_stage` (J5)."""

    stages: list[StageRollupRow]
    total_items: int
    total_value: Decimal | None


async def rollup_by_stage(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> PipelineRollup:
    """Return per-stage item counts + summed value_estimate for a workspace (J5).

    Issues a single grouped SQL query (JOIN pipeline_stage → GROUP BY stage)
    so there is no N+1. Stages with zero items are included (LEFT JOIN) so the
    report always shows all configured stages even when empty. Results are
    ordered by stage position. Workspace-isolated — callers pass workspace_id;
    no cross-workspace data leaks.

    Auto-provisions default stages (same lazy-provisioning pattern as
    ``list_stages`` / ``create_item``) so a fresh workspace returns nine rows
    with zero counts rather than an empty list.
    """
    await provision_default_stages(session, workspace_id=workspace_id)
    # LEFT JOIN so stages with 0 items still appear.
    stmt = (
        select(
            PipelineStage.id,
            PipelineStage.name,
            PipelineStage.position,
            func.count(PipelineItem.id).label("item_count"),
            func.sum(PipelineItem.value_estimate).label("total_value"),
        )
        .select_from(PipelineStage)
        .outerjoin(
            PipelineItem,
            (PipelineItem.stage_id == PipelineStage.id)
            & (PipelineItem.workspace_id == workspace_id),
        )
        .where(PipelineStage.workspace_id == workspace_id)
        .group_by(PipelineStage.id, PipelineStage.name, PipelineStage.position)
        .order_by(PipelineStage.position, PipelineStage.id)
    )

    rows = (await session.execute(stmt)).all()

    stage_rows: list[StageRollupRow] = [
        StageRollupRow(
            stage_id=row.id,
            stage_name=row.name,
            stage_position=row.position,
            item_count=int(row.item_count),
            total_value=Decimal(str(row.total_value)) if row.total_value is not None else None,
        )
        for row in rows
    ]

    total_items = sum(r.item_count for r in stage_rows)
    # Sum only stages that have at least one valued item; return None if nothing.
    valued = [r.total_value for r in stage_rows if r.total_value is not None]
    total_value: Decimal | None = sum(valued, Decimal("0")) if valued else None

    return PipelineRollup(stages=stage_rows, total_items=total_items, total_value=total_value)
