"""HTTP endpoints for the icp module, mounted under `/api/v1/icp` (doc 08 §2 ``/icps``).

The ICP is the workspace's targeting config (doc 14 §3.1). Every endpoint is
behind ``require_workspace`` so the resolved workspace (``X-Workspace-Id``,
doc 08 §1.4) scopes every read/write — workspace A can never see or edit
workspace B's ICP. Errors are RFC 7807 ``application/problem+json`` (doc 08
§1.7) raised as :class:`ProblemException`. Lists are cursor-paginated (doc 06 §5).

These are the same endpoints the web wizard (F2) drives and that the public API
exposes — there is no separate internal surface (doc 08 preamble).
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
from .models import IcpDefinition
from .schemas import IcpCreate, IcpOut, IcpPage, IcpUpdate

router = APIRouter(prefix="/icp", tags=["icp"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]


def _icp_out(icp: IcpDefinition) -> IcpOut:
    return IcpOut.model_validate(icp)


def _not_found() -> ProblemException:
    return ProblemException(
        status=status.HTTP_404_NOT_FOUND,
        code="not_found",
        title="ICP not found",
        detail="No such ICP in this workspace.",
    )


def _active_conflict() -> ProblemException:
    # The partial unique index (one active ICP per workspace) can trip if two
    # create/activate requests for the same workspace race past the in-txn
    # deactivation. Map that to 409 rather than letting it surface as a 500.
    return ProblemException(
        status=status.HTTP_409_CONFLICT,
        code="conflict",
        title="Active ICP conflict",
        detail="Another ICP was activated concurrently; retry the request.",
    )


def _validation_problem(detail: str) -> ProblemException:
    # Plain ``422`` (the RFC 7807 validation status, doc 08 §1.7) — Starlette's
    # ``HTTP_422_*`` constant was renamed and now emits a DeprecationWarning;
    # ``problems.py`` already keys its default code/title on the integer.
    return ProblemException(
        status=422,
        code="validation",
        title="Validation failed",
        detail=detail,
    )


@router.post(
    "",
    response_model=IcpOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create an ICP definition",
)
async def create_icp(
    body: IcpCreate,
    ctx: CurrentWorkspace,
    session: SessionDep,
    response: Response,
) -> IcpOut:
    """Create an ICP in the active workspace (doc 14 §3.1).

    When ``is_active`` is true (the default), any previously-active ICP in the
    workspace is deactivated so exactly one ICP stays active.
    """
    try:
        icp = await services.create_icp(
            session,
            workspace_id=ctx.workspace_id,
            name=body.name,
            countries=body.countries,
            states=body.states,
            entity_kinds=[k.value for k in body.entity_kinds],
            signal_types=[t.value for t in body.signal_types],
            min_size=body.min_size,
            max_size=body.max_size,
            signal_weights=dict(body.signal_weights),
            keywords_required=body.keywords_required,
            keywords_excluded=body.keywords_excluded,
            deal_band_min_cents=body.deal_band_min_cents,
            deal_band_max_cents=body.deal_band_max_cents,
            threshold=body.threshold,
            is_active=body.is_active,
        )
        await session.commit()
    except services.IcpValidationError as exc:
        await session.rollback()
        raise _validation_problem(str(exc)) from exc
    except IntegrityError as exc:  # concurrent activation race
        await session.rollback()
        raise _active_conflict() from exc

    response.headers["Location"] = f"/api/v1/icp/{icp.id}"
    return _icp_out(icp)


@router.get("", response_model=IcpPage, summary="List the workspace's ICP definitions")
async def list_icps(
    ctx: CurrentWorkspace,
    session: SessionDep,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> IcpPage:
    """List the active workspace's ICP definitions (cursor-paginated, doc 06 §5)."""
    try:
        page = await services.list_icps(
            session, workspace_id=ctx.workspace_id, cursor=cursor, limit=limit
        )
    except ValueError as exc:
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            title="Invalid cursor",
            detail="The supplied cursor is malformed.",
        ) from exc
    return IcpPage(items=[_icp_out(i) for i in page.items], next_cursor=page.next_cursor)


@router.get("/{icp_id}", response_model=IcpOut, summary="Get one ICP definition")
async def get_icp(
    icp_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> IcpOut:
    """Fetch one ICP scoped to the active workspace.

    A non-existent id, or one belonging to another workspace, is ``404`` (the
    workspace scope is the tenant boundary; existence is not leaked).
    """
    icp = await services.get_icp(session, workspace_id=ctx.workspace_id, icp_id=icp_id)
    if icp is None:
        raise _not_found()
    return _icp_out(icp)


@router.patch("/{icp_id}", response_model=IcpOut, summary="Update an ICP definition")
async def update_icp(
    icp_id: uuid.UUID,
    body: IcpUpdate,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> IcpOut:
    """Patch an ICP's fields (partial). Activation is changed via ``/activate``."""
    # ``exclude_none`` so an explicit ``null`` is treated as "leave unchanged"
    # (every IcpUpdate field is optional and ``None`` means unchanged) — without
    # it a ``{"countries": null}`` body would null a NOT NULL column → 500.
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    # Normalize enum lists to their string values for the DB array columns.
    if "entity_kinds" in changes:
        changes["entity_kinds"] = [k.value for k in body.entity_kinds or []]
    if "signal_types" in changes:
        changes["signal_types"] = [t.value for t in body.signal_types or []]
    try:
        icp = await services.update_icp(
            session, workspace_id=ctx.workspace_id, icp_id=icp_id, changes=changes
        )
        await session.commit()
    except services.IcpNotFoundError as exc:
        await session.rollback()
        raise _not_found() from exc
    except services.IcpValidationError as exc:
        await session.rollback()
        raise _validation_problem(str(exc)) from exc
    return _icp_out(icp)


@router.post("/{icp_id}/activate", response_model=IcpOut, summary="Make this ICP the active one")
async def activate_icp(
    icp_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> IcpOut:
    """Activate this ICP, deactivating the workspace's other ICPs (doc 14 §3.1).

    # TODO F6: enqueue a backfill against the newly-active ICP's dimensions so the
    #   feed reflects the change (doc 14 §7).
    """
    try:
        icp = await services.set_active(
            session, workspace_id=ctx.workspace_id, icp_id=icp_id, is_active=True
        )
        await session.commit()
    except services.IcpNotFoundError as exc:
        await session.rollback()
        raise _not_found() from exc
    except IntegrityError as exc:  # concurrent activation race
        await session.rollback()
        raise _active_conflict() from exc
    return _icp_out(icp)


@router.post("/{icp_id}/deactivate", response_model=IcpOut, summary="Deactivate this ICP")
async def deactivate_icp(
    icp_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> IcpOut:
    """Deactivate this ICP (the workspace then has no active ICP / empty feed)."""
    try:
        icp = await services.set_active(
            session, workspace_id=ctx.workspace_id, icp_id=icp_id, is_active=False
        )
        await session.commit()
    except services.IcpNotFoundError as exc:
        await session.rollback()
        raise _not_found() from exc
    return _icp_out(icp)
