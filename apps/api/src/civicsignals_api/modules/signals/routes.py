"""HTTP endpoints for the signals module, mounted under ``/api/v1/signals``.

Signals are the **global** signal corpus (doc 07 §3, doc 14 §4.2), so — like the
entities directory — these read endpoints intentionally do **not** require the
``X-Workspace-Id`` header: a signal row is the same for everyone; what differs is
the per-workspace *score* (``signals_workspace_score``, F3), which the G1 feed
joins on top. Writes happen only through the extraction funnel (E1 →
``services.promote_candidate_to_signal``), never via HTTP.

  GET /signals             — list global signals (cursor-paginated; filters)
  GET /signals/{id}        — get one signal

Cursor pagination + RFC 7807 ``application/problem+json`` errors (doc 06 §5).
``api/v1.py`` already imports and mounts this router — do not add it again there.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session

from . import services
from .schemas import SignalPage, SignalRead

router = APIRouter(prefix="/signals", tags=["signals"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=services.MAX_LIMIT)]


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    """RFC 7807 ``application/problem+json`` response (doc 06 §5)."""
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


@router.get("", response_model=SignalPage, summary="List global signals")
async def list_signals(
    session: SessionDep,
    entity_id: Annotated[uuid.UUID | None, Query(description="Filter by entity id.")] = None,
    signal_type: Annotated[str | None, Query(description="Filter by signal type slug.")] = None,
    occurred_after: Annotated[
        datetime | None, Query(description="Only signals occurring at/after this time.")
    ] = None,
    occurred_before: Annotated[
        datetime | None, Query(description="Only signals occurring at/before this time.")
    ] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> SignalPage | JSONResponse:
    """List the global signal corpus, newest first (cursor-paginated)."""
    try:
        return await services.list_signals(
            session,
            entity_id=entity_id,
            signal_type=signal_type,
            occurred_after=occurred_after,
            occurred_before=occurred_before,
            cursor=cursor,
            limit=limit,
        )
    except ValueError:
        return _problem(400, "Invalid cursor", "The supplied cursor is malformed.")


@router.get("/{signal_id}", response_model=SignalRead, summary="Get one signal")
async def get_signal(
    signal_id: uuid.UUID,
    session: SessionDep,
) -> SignalRead | JSONResponse:
    """Fetch one global signal by id."""
    signal = await services.get_signal(session, signal_id)
    if signal is None:
        return _problem(404, "Signal not found", f"No signal with id {signal_id}.")
    return signal
