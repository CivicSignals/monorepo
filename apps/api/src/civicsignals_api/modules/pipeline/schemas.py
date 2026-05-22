"""Pydantic request/response shapes for the pipeline module (doc 06 §3, J1).

Cursor pagination follows the same pattern as the accounts module (doc 06 §5,
doc 08 §1.5): opaque ``next_cursor`` string, ``?cursor=…&limit=…`` query params.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import ItemStatus

# ---------------------------------------------------------------------------
# Stage schemas
# ---------------------------------------------------------------------------


class StageCreate(BaseModel):
    """Request body for ``POST /pipeline/stages``."""

    name: str = Field(min_length=1, max_length=120)
    is_default: bool = False


class StageUpdate(BaseModel):
    """Request body for ``PATCH /pipeline/stages/{stage_id}``."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_default: bool | None = None


class StageReorderItem(BaseModel):
    """One entry in the reorder payload — stage id + desired position."""

    id: UUID
    position: int = Field(ge=0)


class StageReorder(BaseModel):
    """Request body for ``PUT /pipeline/stages/reorder``.

    The caller supplies the full ordered list of stage ids; the server
    rebuilds positions 0..N-1 in that order. All stage ids must belong to the
    workspace; any missing stage id is an error.
    """

    stages: list[StageReorderItem] = Field(min_length=1)


class StageOut(BaseModel):
    """Public representation of a pipeline stage."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    position: int
    is_default: bool
    created_at: datetime
    updated_at: datetime


class StagePage(BaseModel):
    """Cursor-paginated list of stages."""

    items: list[StageOut]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Item schemas
# ---------------------------------------------------------------------------


class ItemCreate(BaseModel):
    """Request body for ``POST /pipeline/items``."""

    title: str = Field(min_length=1, max_length=500)
    stage_id: UUID | None = None  # defaults to workspace's default stage
    signal_id: UUID | None = None  # loose ref — TODO J1-signal-fk
    owner_id: UUID | None = None  # accounts_member.id
    notes: str | None = None
    value_estimate: Decimal | None = Field(default=None, ge=Decimal("0"))


class ItemUpdate(BaseModel):
    """Request body for ``PATCH /pipeline/items/{item_id}``."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    notes: str | None = None
    value_estimate: Decimal | None = Field(default=None, ge=Decimal("0"))
    status: ItemStatus | None = None
    owner_id: UUID | None = Field(
        default=None,
        description="accounts_member.id; omit to leave unchanged, send null to un-assign.",
    )


class ItemMove(BaseModel):
    """Request body for ``POST /pipeline/items/{item_id}/move``.

    J2 (Kanban DnD) calls this endpoint. Optionally updates ``status``
    at the same time (e.g., moving to Won stage → status = won).
    """

    stage_id: UUID
    status: ItemStatus | None = None


class ItemOut(BaseModel):
    """Public representation of a pipeline item."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    stage_id: UUID
    signal_id: UUID | None
    owner_id: UUID | None
    title: str
    notes: str | None
    value_estimate: Decimal | None
    status: ItemStatus
    created_at: datetime
    updated_at: datetime


class ItemPage(BaseModel):
    """Cursor-paginated list of items."""

    items: list[ItemOut]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Reporting schemas (J5)
# ---------------------------------------------------------------------------


class StageRollup(BaseModel):
    """Per-stage totals in the pipeline rollup report (J5).

    ``item_count`` — number of items currently in this stage.
    ``total_value`` — sum of all non-null ``value_estimate`` values (USD, 2dp).
                      ``None`` when no items have a value estimate.
    """

    stage_id: UUID
    stage_name: str
    stage_position: int
    item_count: int
    total_value: Decimal | None


class PipelineReport(BaseModel):
    """Workspace-level pipeline rollup (J5, ``GET /pipeline/report``).

    ``stages``       — per-stage counts and summed value, ordered by position.
    ``total_items``  — total item count across all stages.
    ``total_value``  — summed value across all stages; ``None`` if no values set.
    """

    stages: list[StageRollup]
    total_items: int
    total_value: Decimal | None
