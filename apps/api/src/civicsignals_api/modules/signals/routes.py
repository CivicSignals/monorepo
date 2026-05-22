"""HTTP endpoints for the signals module, mounted under ``/api/v1/signals``.

Signals are the **global** signal corpus (doc 07 §3, doc 14 §4.2), so — like the
entities directory — these read endpoints intentionally do **not** require the
``X-Workspace-Id`` header: a signal row is the same for everyone; what differs is
the per-workspace *score* (``signals_workspace_score``, F3), which the G1 feed
joins on top. Writes happen only through the extraction funnel (E1 →
``services.promote_candidate_to_signal``), never via HTTP.

  GET  /signals                              — list global signals (cursor-paginated)
  GET  /signals/{id}                         — get one signal
  GET  /signals/fuzzy-reviews                — list fuzzy-dedupe review rows (admin)
  GET  /signals/fuzzy-reviews/{id}           — get one review row
  POST /signals/fuzzy-reviews/{id}/approve   — approve → merge candidate into match
  POST /signals/fuzzy-reviews/{id}/reject    — reject → keep candidate as distinct

Fuzzy-review endpoints are admin-gated (doc 19 §7.4 — the review queue is an
internal tool to validate the 0.92 cosine threshold before enabling auto-merge).
Cursor pagination + RFC 7807 ``application/problem+json`` errors (doc 06 §5).
``api/v1.py`` already imports and mounts this router — do not add it again there.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
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


# ---------------------------------------------------------------------------
# Signal corpus endpoints (read-only; writes via extraction pipeline only)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Fuzzy-review schemas (E10, doc 19 §7.4)
# ---------------------------------------------------------------------------


class FuzzyReviewRead(BaseModel):
    """One ``signals_fuzzy_review`` row (E10; doc 19 §7.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_signal_id: uuid.UUID
    matched_signal_id: uuid.UUID
    similarity: float
    signal_type: str
    status: str
    reviewed_at: datetime | None
    reviewer_note: str | None
    created_at: datetime


class FuzzyReviewPage(BaseModel):
    """Cursor-paginated page of fuzzy-review rows (E10; doc 06 §5)."""

    model_config = ConfigDict(extra="forbid")

    items: list[FuzzyReviewRead]
    next_cursor: str | None = None


class FuzzyReviewDecisionIn(BaseModel):
    """Request body for approve/reject decisions (E10; doc 19 §7.4)."""

    model_config = ConfigDict(extra="forbid")

    reviewer_note: str | None = None


# ---------------------------------------------------------------------------
# Fuzzy-review endpoints (admin-gated; E10, doc 19 §7.4)
# ---------------------------------------------------------------------------

# TODO B7: add ``require_admin`` dependency once RBAC lands. The review endpoints
# are internal tooling (validating the 0.92 threshold) and should be admin-only.


@router.get(
    "/fuzzy-reviews",
    response_model=FuzzyReviewPage,
    summary="List fuzzy-dedupe review rows (admin)",
)
async def list_fuzzy_reviews(
    session: SessionDep,
    signal_type: Annotated[str | None, Query(description="Filter by signal type.")] = None,
    status: Annotated[
        str | None,
        Query(description="Filter by status: pending | approved | rejected."),
    ] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> FuzzyReviewPage | JSONResponse:
    """List fuzzy-review rows (doc 19 §7.4), newest first (cursor-paginated).

    Admin endpoint: validates the cosine threshold by reviewing first-100 fuzzy
    matches per signal type before auto-merge is enabled.
    """
    try:
        items, next_cursor = await services.list_fuzzy_reviews(
            session,
            signal_type=signal_type,
            status=status,
            cursor=cursor,
            limit=limit,
        )
    except ValueError:
        return _problem(400, "Invalid cursor", "The supplied cursor is malformed.")
    return FuzzyReviewPage(
        items=[FuzzyReviewRead.model_validate(r) for r in items],
        next_cursor=next_cursor,
    )


@router.get(
    "/fuzzy-reviews/{review_id}",
    response_model=FuzzyReviewRead,
    summary="Get one fuzzy-review row",
)
async def get_fuzzy_review(
    review_id: uuid.UUID,
    session: SessionDep,
) -> FuzzyReviewRead | JSONResponse:
    """Fetch one fuzzy-review row by id (E10; doc 19 §7.4)."""
    row = await services.get_fuzzy_review(session, review_id)
    if row is None:
        return _problem(404, "Fuzzy review not found", f"No fuzzy review with id {review_id}.")
    return FuzzyReviewRead.model_validate(row)


@router.post(
    "/fuzzy-reviews/{review_id}/approve",
    response_model=FuzzyReviewRead,
    summary="Approve a fuzzy-dedupe match (merge candidate into matched signal)",
)
async def approve_fuzzy_review(
    review_id: uuid.UUID,
    session: SessionDep,
    body: Annotated[FuzzyReviewDecisionIn, Body(default_factory=FuzzyReviewDecisionIn)],
) -> FuzzyReviewRead | JSONResponse:
    """Approve a pending fuzzy-dedupe review (E10; doc 19 §7.4).

    Merges the candidate signal's source documents into the matched (surviving)
    signal, using the same merge semantics as exact-match dedupe (doc 19 §7.3).
    Counts toward the graduation threshold for the signal type.
    """
    try:
        row = await services.decide_fuzzy_review(
            session,
            review_id,
            approved=True,
            reviewer_note=body.reviewer_note,
        )
    except services.FuzzyReviewNotFoundError:
        return _problem(404, "Fuzzy review not found", f"No fuzzy review with id {review_id}.")
    except services.FuzzyReviewAlreadyDecidedError as exc:
        return _problem(
            409,
            "Fuzzy review already decided",
            f"Review {review_id} is already in status {exc.current_status!r}.",
        )
    except services.FuzzyReviewSignalMissingError as exc:
        return _problem(
            409,
            "Signal row(s) missing",
            str(exc),
        )
    await session.commit()
    return FuzzyReviewRead.model_validate(row)


@router.post(
    "/fuzzy-reviews/{review_id}/reject",
    response_model=FuzzyReviewRead,
    summary="Reject a fuzzy-dedupe match (keep candidate as distinct signal)",
)
async def reject_fuzzy_review(
    review_id: uuid.UUID,
    session: SessionDep,
    body: Annotated[FuzzyReviewDecisionIn, Body(default_factory=FuzzyReviewDecisionIn)],
) -> FuzzyReviewRead | JSONResponse:
    """Reject a pending fuzzy-dedupe review (E10; doc 19 §7.4).

    Leaves the candidate signal as an independent signal (no merge). Counts toward
    the graduation threshold — even rejected reviews inform threshold calibration.
    """
    try:
        row = await services.decide_fuzzy_review(
            session,
            review_id,
            approved=False,
            reviewer_note=body.reviewer_note,
        )
    except services.FuzzyReviewNotFoundError:
        return _problem(404, "Fuzzy review not found", f"No fuzzy review with id {review_id}.")
    except services.FuzzyReviewAlreadyDecidedError as exc:
        return _problem(
            409,
            "Fuzzy review already decided",
            f"Review {review_id} is already in status {exc.current_status!r}.",
        )
    await session.commit()
    return FuzzyReviewRead.model_validate(row)
