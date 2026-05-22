"""HTTP endpoints for the pipeline module (J1, J3), mounted under ``/api/v1/pipeline``.

All endpoints require workspace context (``require_workspace`` / B5):
- ``X-Workspace-Id`` header routes to the correct tenant.
- RFC 7807 ``application/problem+json`` errors via ``ProblemException``.
- Cursor pagination (``?cursor=…&limit=…``, doc 06 §5).

Stage endpoints:
  GET  /pipeline/stages              — list (cursor-paginated, auto-provisions defaults)
  POST /pipeline/stages              — create
  GET  /pipeline/stages/{id}         — get one
  PATCH /pipeline/stages/{id}        — update name / default flag
  DELETE /pipeline/stages/{id}       — delete (only if empty)
  PUT  /pipeline/stages/reorder      — reorder all stages

Item endpoints:
  GET  /pipeline/items               — list (cursor-paginated, optional filters)
  POST /pipeline/items               — create
  GET  /pipeline/items/{id}          — get one
  PATCH /pipeline/items/{id}         — update
  DELETE /pipeline/items/{id}        — delete
  POST /pipeline/items/{id}/move     — move to stage (J2 Kanban DnD seam)

Activity endpoints (J3):
  GET  /pipeline/items/{id}/activity  — item activity timeline (cursor-paginated)
  POST /pipeline/items/{id}/comments  — add a free-text comment

Note: ``api/v1.py`` already imports and mounts ``pipeline_routes.router``; this
file must NOT be added to ``api/v1.py`` again.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import CurrentWorkspace
from civicsignals_api.problems import ProblemException

from . import services
from .models import PipelineItem, PipelineItemActivity, PipelineStage
from .schemas import (
    ActivityOut,
    ActivityPage,
    CommentCreate,
    ItemCreate,
    ItemMove,
    ItemOut,
    ItemPage,
    ItemUpdate,
    PipelineReport,
    StageCreate,
    StageOut,
    StagePage,
    StageReorder,
    StageRollup,
    StageUpdate,
)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]


def _stage_out(stage: PipelineStage) -> StageOut:
    return StageOut.model_validate(stage)


def _item_out(item: PipelineItem) -> ItemOut:
    return ItemOut.model_validate(item)


def _activity_out(entry: PipelineItemActivity) -> ActivityOut:
    return ActivityOut.model_validate(entry)


def _not_found(entity: str) -> ProblemException:
    return ProblemException(
        status=status.HTTP_404_NOT_FOUND,
        code="not_found",
        title=f"{entity} not found",
        detail=f"No such {entity.lower()} in this workspace.",
    )


def _conflict(detail: str) -> ProblemException:
    return ProblemException(
        status=status.HTTP_409_CONFLICT,
        code="conflict",
        title="Conflict",
        detail=detail,
    )


def _bad_request(detail: str) -> ProblemException:
    return ProblemException(
        status=status.HTTP_400_BAD_REQUEST,
        code="bad_request",
        title="Bad request",
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Reporting endpoint (J5)
# ---------------------------------------------------------------------------


@router.get(
    "/report",
    response_model=PipelineReport,
    summary="Pipeline rollup report",
    tags=["pipeline"],
)
async def get_pipeline_report(
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> PipelineReport:
    """Workspace-level pipeline totals by stage (J5).

    Returns per-stage item counts and summed value_estimate, plus workspace
    totals. Computed with a single grouped SQL query — no N+1. Stages with
    zero items are included so the chart always shows all columns.

    RFC 7807 errors on unexpected failures.
    """
    rollup = await services.rollup_by_stage(session, ctx.workspace_id)
    # Commit so lazy-provisioned stages are visible across requests (same
    # pattern as list_stages; no-op when workspace already had stages).
    await session.commit()
    stage_out = [
        StageRollup(
            stage_id=r.stage_id,
            stage_name=r.stage_name,
            stage_position=r.stage_position,
            item_count=r.item_count,
            total_value=r.total_value,
        )
        for r in rollup.stages
    ]
    return PipelineReport(
        stages=stage_out,
        total_items=rollup.total_items,
        total_value=rollup.total_value,
    )


# ---------------------------------------------------------------------------
# Stage endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/stages",
    response_model=StagePage,
    summary="List pipeline stages",
)
async def list_stages(
    ctx: CurrentWorkspace,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> StagePage:
    """List the workspace's Kanban stages (cursor-paginated, ordered by position).

    Auto-provisions the nine default stages on first call for a new workspace.
    The provision is committed immediately so subsequent requests see the rows.
    """
    try:
        page = await services.list_stages(session, ctx.workspace_id, cursor=cursor, limit=limit)
        # Commit here so lazy-provisioned stages are visible across requests.
        # This is a no-op when the workspace already had stages (nothing was added).
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise _bad_request("Invalid cursor.") from exc
    return StagePage(items=[_stage_out(s) for s in page.items], next_cursor=page.next_cursor)


@router.post(
    "/stages",
    response_model=StageOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pipeline stage",
)
async def create_stage(
    body: StageCreate,
    ctx: CurrentWorkspace,
    session: SessionDep,
    response: Response,
) -> StageOut:
    """Append a new Kanban stage at the end of the workspace's stage list."""
    try:
        stage = await services.create_stage(
            session,
            ctx.workspace_id,
            name=body.name,
            is_default=body.is_default,
        )
        await session.commit()
    except (services.StageNameConflictError, IntegrityError) as exc:
        await session.rollback()
        raise _conflict(f"A stage named '{body.name}' already exists in this workspace.") from exc
    response.headers["Location"] = f"/api/v1/pipeline/stages/{stage.id}"
    return _stage_out(stage)


@router.get(
    "/stages/{stage_id}",
    response_model=StageOut,
    summary="Get a pipeline stage",
)
async def get_stage(
    stage_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> StageOut:
    """Fetch one stage by id (workspace-scoped)."""
    try:
        stage = await services.get_stage(session, ctx.workspace_id, stage_id)
    except services.StageNotFoundError as exc:
        raise _not_found("Stage") from exc
    return _stage_out(stage)


@router.patch(
    "/stages/{stage_id}",
    response_model=StageOut,
    summary="Update a pipeline stage",
)
async def update_stage(
    stage_id: uuid.UUID,
    body: StageUpdate,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> StageOut:
    """Update a stage's name and/or default flag."""
    try:
        stage = await services.update_stage(
            session,
            ctx.workspace_id,
            stage_id,
            name=body.name,
            is_default=body.is_default,
        )
        await session.commit()
    except services.StageNotFoundError as exc:
        await session.rollback()
        raise _not_found("Stage") from exc
    except (services.StageNameConflictError, IntegrityError) as exc:
        await session.rollback()
        raise _conflict(f"A stage named '{body.name}' already exists in this workspace.") from exc
    return _stage_out(stage)


@router.delete(
    "/stages/{stage_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a pipeline stage",
)
async def delete_stage(
    stage_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> None:
    """Delete a stage. Fails with 409 if the stage still has items."""
    try:
        await services.delete_stage(session, ctx.workspace_id, stage_id)
        await session.commit()
    except services.StageNotFoundError as exc:
        await session.rollback()
        raise _not_found("Stage") from exc
    except (services.StageInUseError, IntegrityError) as exc:
        await session.rollback()
        raise _conflict("Cannot delete a stage that still has pipeline items.") from exc


@router.put(
    "/stages/reorder",
    response_model=StagePage,
    summary="Reorder all pipeline stages",
)
async def reorder_stages(
    body: StageReorder,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> StagePage:
    """Reorder the workspace's stages.

    Supply the full ordered list of stage ids; positions are rebuilt 0-based
    in the given order. All workspace stage ids must be present exactly once.
    """
    # Sort by the client-supplied position so the explicit field is the source of
    # truth. Positions need not be contiguous — only the sort order matters here;
    # the service rebuilds 0-based positions from the resulting list.
    sorted_stages = sorted(body.stages, key=lambda s: s.position)
    ordered_ids = [item.id for item in sorted_stages]
    try:
        stages = await services.reorder_stages(session, ctx.workspace_id, ordered_ids)
        await session.commit()
    except services.ReorderError as exc:
        await session.rollback()
        raise _bad_request(str(exc)) from exc
    return StagePage(items=[_stage_out(s) for s in stages], next_cursor=None)


# ---------------------------------------------------------------------------
# Item endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/items",
    response_model=ItemPage,
    summary="List pipeline items",
)
async def list_items(
    ctx: CurrentWorkspace,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
    stage_id: Annotated[uuid.UUID | None, Query(description="Filter by stage.")] = None,
    owner_id: Annotated[uuid.UUID | None, Query(description="Filter by owner/assignee.")] = None,
) -> ItemPage:
    """List pipeline items (cursor-paginated). Optional filters: stage_id, owner_id."""
    try:
        page = await services.list_items(
            session,
            ctx.workspace_id,
            stage_id=stage_id,
            owner_id=owner_id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise _bad_request("Invalid cursor.") from exc
    return ItemPage(items=[_item_out(i) for i in page.items], next_cursor=page.next_cursor)


@router.post(
    "/items",
    response_model=ItemOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pipeline item",
)
async def create_item(
    body: ItemCreate,
    ctx: CurrentWorkspace,
    session: SessionDep,
    response: Response,
) -> ItemOut:
    """Create a pipeline item in the workspace.

    If ``stage_id`` is omitted the workspace's default stage is used.
    Auto-provisions default stages if the workspace has none yet.
    """
    try:
        item = await services.create_item(
            session,
            ctx.workspace_id,
            title=body.title,
            stage_id=body.stage_id,
            signal_id=body.signal_id,
            owner_id=body.owner_id,
            notes=body.notes,
            value_estimate=body.value_estimate,
        )
        await session.commit()
    except services.StageNotFoundError as exc:
        await session.rollback()
        raise _not_found("Stage") from exc
    except services.PipelineError as exc:
        await session.rollback()
        raise _bad_request(str(exc)) from exc
    response.headers["Location"] = f"/api/v1/pipeline/items/{item.id}"
    return _item_out(item)


@router.get(
    "/items/{item_id}",
    response_model=ItemOut,
    summary="Get a pipeline item",
)
async def get_item(
    item_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> ItemOut:
    """Fetch one pipeline item by id (workspace-scoped)."""
    try:
        item = await services.get_item(session, ctx.workspace_id, item_id)
    except services.ItemNotFoundError as exc:
        raise _not_found("Item") from exc
    return _item_out(item)


@router.patch(
    "/items/{item_id}",
    response_model=ItemOut,
    summary="Update a pipeline item",
)
async def update_item(
    item_id: uuid.UUID,
    body: ItemUpdate,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> ItemOut:
    """Partial update: title, notes, value_estimate, status, owner_id."""
    # Use services.UNSET sentinel for nullable fields so the service can
    # distinguish "field omitted" (no-op) from "field set to null" (clear col).
    set_fields = body.model_fields_set
    try:
        item = await services.update_item(
            session,
            ctx.workspace_id,
            item_id,
            title=body.title,
            notes=body.notes if "notes" in set_fields else services.UNSET,
            value_estimate=(
                body.value_estimate if "value_estimate" in set_fields else services.UNSET
            ),
            status=body.status,
            owner_id=body.owner_id if "owner_id" in set_fields else services.UNSET,
        )
        await session.commit()
    except services.ItemNotFoundError as exc:
        await session.rollback()
        raise _not_found("Item") from exc
    return _item_out(item)


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a pipeline item",
)
async def delete_item(
    item_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> None:
    """Hard-delete a pipeline item."""
    try:
        await services.delete_item(session, ctx.workspace_id, item_id)
        await session.commit()
    except services.ItemNotFoundError as exc:
        await session.rollback()
        raise _not_found("Item") from exc


@router.post(
    "/items/{item_id}/move",
    response_model=ItemOut,
    summary="Move a pipeline item to a different stage",
)
async def move_item(
    item_id: uuid.UUID,
    body: ItemMove,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> ItemOut:
    """Move an item to a different stage (J2 Kanban DnD seam).

    Optionally updates ``status`` at the same time (e.g., moving to Won →
    ``status=won``).
    """
    try:
        item = await services.move_item(
            session,
            ctx.workspace_id,
            item_id,
            stage_id=body.stage_id,
            status=body.status,
        )
        await session.commit()
    except services.ItemNotFoundError as exc:
        await session.rollback()
        raise _not_found("Item") from exc
    except services.StageNotFoundError as exc:
        await session.rollback()
        raise _not_found("Stage") from exc
    return _item_out(item)


# ---------------------------------------------------------------------------
# Activity endpoints (J3)
# ---------------------------------------------------------------------------


@router.get(
    "/items/{item_id}/activity",
    response_model=ActivityPage,
    summary="Get pipeline item activity timeline",
    tags=["pipeline"],
)
async def list_activity(
    item_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> ActivityPage:
    """Chronological activity timeline for a pipeline item (J3).

    Returns stage transitions, comments, assignments, value changes, and
    integration pushes. Workspace-scoped — only returns activity for items
    belonging to the authenticated workspace.

    Cursor-paginated (oldest-first): the first page is the oldest activity,
    subsequent pages go forward in time via ``?cursor=…``.

    RFC 7807 errors:
    - 404 when the item does not exist in this workspace.
    - 400 on an invalid cursor.
    """
    # Verify the item exists in this workspace first.
    try:
        await services.get_item(session, ctx.workspace_id, item_id)
    except services.ItemNotFoundError as exc:
        raise _not_found("Item") from exc
    try:
        page = await services.list_activity(
            session,
            item_id=item_id,
            workspace_id=ctx.workspace_id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise _bad_request("Invalid cursor.") from exc
    return ActivityPage(items=[_activity_out(e) for e in page.items], next_cursor=page.next_cursor)


@router.post(
    "/items/{item_id}/comments",
    response_model=ActivityOut,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment to a pipeline item",
    tags=["pipeline"],
)
async def add_comment(
    item_id: uuid.UUID,
    body: CommentCreate,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> ActivityOut:
    """Add a free-text comment to a pipeline item (J3).

    The comment is stored as an activity entry (``activity_type = 'comment'``)
    and appears in the item's activity timeline. Workspace-scoped.

    RFC 7807 errors:
    - 404 when the item does not exist in this workspace.
    """
    try:
        entry = await services.add_comment(
            session,
            item_id=item_id,
            workspace_id=ctx.workspace_id,
            text=body.text,
            actor_id=body.actor_id,
        )
        await session.commit()
    except services.ItemNotFoundError as exc:
        await session.rollback()
        raise _not_found("Item") from exc
    return _activity_out(entry)
