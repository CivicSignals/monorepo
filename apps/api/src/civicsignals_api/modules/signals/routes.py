"""HTTP endpoints for the signals module, mounted under ``/api/v1/signals``.

Signals are the **global** signal corpus (doc 07 §3, doc 14 §4.2), so — like the
entities directory — these read endpoints intentionally do **not** require the
``X-Workspace-Id`` header: a signal row is the same for everyone; what differs is
the per-workspace *score* (``signals_workspace_score``, F3), which the G1 feed
joins on top. Writes happen only through the extraction funnel (E1 →
``services.promote_candidate_to_signal``), never via HTTP.

  GET  /signals/feed                         — workspace feed: scored signals sorted by score desc (G1)
  GET  /signals                              — list global signals (cursor-paginated)
  GET  /signals/fuzzy-reviews                — list fuzzy-dedupe review rows (admin)
  GET  /signals/fuzzy-reviews/{id}           — get one review row
  POST /signals/fuzzy-reviews/{id}/approve   — approve → merge candidate into match
  POST /signals/fuzzy-reviews/{id}/reject    — reject → keep candidate as distinct
  GET  /signals/{id}/detail                  — workspace-scoped signal detail (G2)
  PATCH /signals/{id}/status                 — transition the per-workspace status (G4)
  POST /signals/bulk-status                  — bulk-transition many signals' status (G3)
  POST /signals/{id}/feedback                — record / change / retract user feedback (F5)
  GET  /signals/{id}                         — get one signal

Route ordering note: ``/feed``, ``/fuzzy-reviews`` and their sub-paths MUST be
registered before ``/{signal_id}`` (the parameterised catch-all) so that FastAPI's
routing evaluates the static prefix first. Moving ``/{signal_id}`` to the end of
the file preserves this invariant regardless of how many static-prefix endpoints
are added later. ``/{signal_id}/feedback`` is a deeper path than ``/{signal_id}``,
so FastAPI matches it first regardless, but it is kept before the catch-all by
convention.

Fuzzy-review endpoints are admin-gated (doc 19 §7.4 — the review queue is an
internal tool to validate the 0.92 cosine threshold before enabling auto-merge).
Cursor pagination + RFC 7807 ``application/problem+json`` errors (doc 06 §5).
``api/v1.py`` already imports and mounts this router — do not add it again there.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api import events
from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import (
    RequireAdmin,
    RequireMember,
    RequireViewer,
)

from . import services
from .schemas import (
    RelatedSignalRead,
    SignalDetailRead,
    SignalPage,
    SignalRead,
    SourceDocumentRead,
    SuggestedContactRead,
)
from .services import WorkspaceFeedPage, WorkspaceSignalDetail

log = structlog.get_logger(__name__)

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
# G1 feed schemas (the workspace-scored read shapes returned by /signals/feed)
# ---------------------------------------------------------------------------


class FeedItemRead(BaseModel):
    """One scored signal in the workspace feed (G1; doc 14 §5.3).

    Wraps :class:`~signals.services.WorkspaceFeedItem` with a Pydantic response
    shape: the global signal core plus its per-workspace score, status, score
    breakdown, and matched keywords so the feed UI (G1) and "Why this signal?"
    panel (G2) render from one read without extra joins.
    """

    model_config = ConfigDict(from_attributes=False)

    score_id: uuid.UUID
    signal: SignalRead
    score: float
    status: str
    score_breakdown: dict[str, object]
    matched_keywords: list[str]
    created_at: datetime


class FeedPage(BaseModel):
    """Cursor-paginated page of workspace feed items (G1; doc 06 §5)."""

    model_config = ConfigDict(extra="forbid")

    data: list[FeedItemRead]
    page: dict[str, object]


def _feed_page(feed: WorkspaceFeedPage, limit: int) -> FeedPage:
    """Convert a service-layer :class:`WorkspaceFeedPage` to the HTTP response shape.

    Uses the doc 08 §1.5 envelope (``data`` + ``page`` dict) instead of the simpler
    ``items``/``next_cursor`` used by older list endpoints so the feed matches the
    spec's worked example exactly (doc 08 §3.1).
    """
    return FeedPage(
        data=[
            FeedItemRead(
                score_id=item.score_id,
                signal=item.signal,
                score=item.score,
                status=item.status,
                score_breakdown=item.score_breakdown,
                matched_keywords=item.matched_keywords,
                created_at=item.created_at,
            )
            for item in feed.items
        ],
        page={
            "next_cursor": feed.next_cursor,
            "has_more": feed.next_cursor is not None,
            "limit": limit,
        },
    )


# ---------------------------------------------------------------------------
# G1 feed endpoint — workspace-scoped, score-ranked, cursor-paginated
#
# IMPORTANT: registered before /{signal_id} so the static /feed prefix wins.
# ---------------------------------------------------------------------------


@router.get("/feed", response_model=FeedPage, summary="Workspace signal feed (G1)")
async def get_workspace_feed(
    ctx: RequireViewer,
    session: SessionDep,
    signal_type: Annotated[
        str | None,
        Query(description="Filter by signal type slug (e.g. rfp_posted, news_mention)."),
    ] = None,
    status: Annotated[
        list[str] | None,
        Query(description="Filter by feed status (new, reviewed, pinned, pushed, dismissed)."),
    ] = None,
    min_score: Annotated[
        float | None,
        Query(ge=0.0, le=100.0, description="Minimum score (0-100)."),
    ] = None,
    published_at_gte: Annotated[
        datetime | None,
        Query(description="Only signals with occurred_at at/after this timestamp."),
    ] = None,
    published_at_lt: Annotated[
        datetime | None,
        Query(description="Only signals with occurred_at before this timestamp."),
    ] = None,
    cursor: CursorQuery = None,
    limit: LimitQuery = services.DEFAULT_LIMIT,
) -> FeedPage | JSONResponse:
    """Workspace signal feed: per-workspace-scored signals sorted by score desc (G1).

    Returns the calling workspace's signals joined with their ``signals_workspace_score``
    rows, ordered highest-score-first. Only threshold-gated signals (those with a
    score row) appear; signals scored below the workspace ICP threshold are invisible
    (doc 14 §5.2). Default status filter is ``new`` + ``reviewed`` + ``pinned``
    (the feed-visible set, doc 14 §5.3).

    Filters (doc 08 §1.6):
    - ``signal_type`` — narrow to one type slug.
    - ``status`` — repeatable; overrides the default visible-status set.
    - ``min_score`` — floor on the 0-100 workspace score.
    - ``published_at_gte``/``published_at_lt`` — bound the signal's ``occurred_at``.

    Workspace isolation is enforced by ``require_workspace``/``RequireViewer`` (B5):
    the workspace id comes from the resolved :class:`WorkspaceContext`, never from
    a query param, so one workspace cannot read another's feed (doc 08 §1.4).

    G3: bulk actions (mass dismiss / pin) are the ``POST /signals/bulk-status`` endpoint
    below (``change_status_bulk``), built on the same per-workspace score rows.
    G4: single-row status transitions are the ``PATCH /signals/{id}/status`` endpoint
    below (``change_status``).
    TODO G5: polished loading/empty/error states (the UI leaves seams for these).
    """
    workspace_id = ctx.workspace.id
    try:
        feed = await services.list_workspace_signals(
            session,
            workspace_id=workspace_id,
            signal_type=signal_type,
            statuses=status,
            min_score=min_score,
            published_at_gte=published_at_gte,
            published_at_lt=published_at_lt,
            cursor=cursor,
            limit=limit,
        )
    except ValueError:
        return _problem(400, "Invalid cursor", "The supplied cursor is malformed.")
    return _feed_page(feed, limit)


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
#
# IMPORTANT: these routes are registered BEFORE /{signal_id} (below) so the
# static ``/fuzzy-reviews`` prefix is matched first by FastAPI's router.
# ---------------------------------------------------------------------------


@router.get(
    "/fuzzy-reviews",
    response_model=FuzzyReviewPage,
    summary="List fuzzy-dedupe review rows (admin)",
)
async def list_fuzzy_reviews(
    ctx: RequireAdmin,
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
    ctx: RequireAdmin,
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
    ctx: RequireAdmin,
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
    ctx: RequireAdmin,
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


# ---------------------------------------------------------------------------
# G2 signal-detail endpoint — workspace-scoped composite read.
#
# Registered before the bare ``/{signal_id}`` (below): ``/{signal_id}/detail`` is
# a deeper path so FastAPI matches it first, but ordering it here keeps the
# convention (more-specific routes precede the catch-all) explicit.
# ---------------------------------------------------------------------------


def _signal_detail(detail: WorkspaceSignalDetail) -> SignalDetailRead:
    """Convert the service-layer :class:`WorkspaceSignalDetail` to the HTTP shape."""
    return SignalDetailRead(
        signal=detail.signal,
        entity_id=detail.entity_id,
        entity_name=detail.entity_name,
        score=detail.score,
        status=detail.status,
        score_breakdown=detail.score_breakdown,
        matched_keywords=detail.matched_keywords,
        extracted_fields=detail.extracted_fields,
        source_documents=[SourceDocumentRead.model_validate(d) for d in detail.source_documents],
        suggested_contacts=[
            SuggestedContactRead.model_validate(c) for c in detail.suggested_contacts
        ],
        related_signals=[RelatedSignalRead(signal=r.signal) for r in detail.related_signals],
        feedback=detail.feedback,
    )


@router.get(
    "/{signal_id}/detail",
    response_model=SignalDetailRead,
    summary="Workspace-scoped signal detail (G2)",
)
async def get_signal_detail(
    signal_id: uuid.UUID,
    ctx: RequireViewer,
    session: SessionDep,
) -> SignalDetailRead | JSONResponse:
    """Full signal-detail view in the calling workspace's context (G2).

    Returns the global signal plus the calling workspace's score / status / breakdown
    (``None`` when the signal did not score into this workspace's feed — the corpus is
    global, doc 07 §3, so a signal stays viewable by id), the validated extracted
    fields, the corroborating source documents, the suggested contacts at the
    signal's entity, and related signals about the same entity (doc 14 §5.3). The
    per-workspace score row is read scoped to the calling workspace (``RequireViewer``
    resolves the workspace from :class:`WorkspaceContext`, never a query param), so
    one workspace can never read another's score (doc 08 §1.4).

    404 (RFC 7807) when the signal does not exist or is a soft-deleted ``merged`` row.

    TODO F4: the "Why this signal?" panel renders bullets from ``score_breakdown``.
    G4: per-signal status transitions are the ``PATCH /signals/{id}/status`` endpoint
    below, which PATCHes the same score row this view reads ``status`` from.
    """
    detail = await services.get_signal_detail(
        session,
        signal_id=signal_id,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
    )
    if detail is None:
        return _problem(404, "Signal not found", f"No signal with id {signal_id}.")
    return _signal_detail(detail)


# ---------------------------------------------------------------------------
# G4 status-transition endpoint — workspace-scoped PATCH of the score row.
#
# Registered before the bare ``/{signal_id}`` (below): ``/{signal_id}/status`` is a
# deeper path so FastAPI matches it first, but ordering it here keeps the convention
# (more-specific routes precede the catch-all) explicit.
# ---------------------------------------------------------------------------


# The statuses the triage UI is allowed to request (G4, doc 14 §5.3). ``pushed`` is
# excluded — it is set by the K-epic CRM-push flow, never by this human-driven PATCH;
# the ``Literal`` makes that a 422 (request validation) at the schema layer so the
# illegal target never reaches the service.
SettableStatus = Literal["new", "reviewed", "pinned", "dismissed"]


class StatusChangeIn(BaseModel):
    """Request body for the G4 status transition (doc 14 §5.3)."""

    model_config = ConfigDict(extra="forbid")

    status: SettableStatus


class StatusChangeRead(BaseModel):
    """The transitioned score row's identity + new status (G4)."""

    model_config = ConfigDict(extra="forbid")

    score_id: uuid.UUID
    signal_id: uuid.UUID
    status: str


@router.patch(
    "/{signal_id}/status",
    response_model=StatusChangeRead,
    summary="Transition a signal's per-workspace status (G4)",
)
async def change_status(
    signal_id: uuid.UUID,
    ctx: RequireMember,
    session: SessionDep,
    body: Annotated[StatusChangeIn, Body()],
) -> StatusChangeRead | JSONResponse:
    """Transition the calling workspace's ``signals_workspace_score.status`` (G4).

    Moves one score row through the feed lifecycle (``new`` → ``reviewed`` →
    ``pinned``, plus ``dismissed`` / restore-to-``new``) per the allowed-transition
    graph (``signals.services.STATUS_TRANSITIONS``, doc 14 §5.3). Member-gated
    (``RequireMember``: viewers cannot mutate workspace data, B7) and workspace-scoped
    — the row is read by ``(workspace_id, signal_id)`` from the resolved
    :class:`WorkspaceContext` (never a body/query param), so one workspace can never
    transition another's row (doc 08 §1.4).

    The ``pushed`` state is *not* settable here — it is owned by the K-epic CRM-push
    flow (doc 14 §5.3); the request schema's ``Literal`` rejects it as a 422 before
    the service runs.

    Errors (RFC 7807):
    - 404 — the signal did not score into this workspace's feed (no score row), or
      the signal does not exist.
    - 422 — the requested transition is not allowed from the current status (the
      ``detail`` names the current state + the allowed targets).

    Emits :data:`~civicsignals_api.events.SIGNAL_STATUS_CHANGED` so B9's audit listener
    records the change (best-effort; a failed audit never fails the transition).
    """
    try:
        row = await services.change_workspace_score_status(
            session,
            workspace_id=ctx.workspace.id,
            signal_id=signal_id,
            target_status=body.status,
        )
    except services.WorkspaceScoreNotFoundError:
        return _problem(
            404,
            "Signal not in feed",
            f"Signal {signal_id} has no score row in this workspace; nothing to transition.",
        )
    except services.IllegalStatusTransitionError as exc:
        return _problem(
            422,
            "Illegal status transition",
            (
                f"Cannot move status from {exc.current!r} to {exc.target!r}; "
                f"allowed targets: {', '.join(sorted(exc.allowed)) or '(none)'}."
            ),
        )
    await session.commit()

    # Best-effort audit (B9 persists). A broken audit sink must not fail the action.
    try:
        await events.publish(
            events.SIGNAL_STATUS_CHANGED,
            {
                "signal_id": str(signal_id),
                "workspace_id": str(ctx.workspace.id),
                "user_id": str(ctx.user.id),
                "status": row.status,
            },
        )
    except Exception:  # pragma: no cover - defensive; audit is fire-and-forget
        log.warning("signal_status_changed_event_failed", signal_id=str(signal_id))

    return StatusChangeRead(score_id=row.id, signal_id=signal_id, status=row.status)


# ---------------------------------------------------------------------------
# G3 bulk status-transition endpoint — workspace-scoped mass dismiss / pin.
#
# IMPORTANT: registered before the bare ``/{signal_id}`` (below) so FastAPI matches
# the static ``/bulk-status`` prefix first (it would otherwise be captured as a
# ``signal_id`` path param).
# ---------------------------------------------------------------------------


class BulkStatusChangeIn(BaseModel):
    """Request body for the G3 bulk status transition (doc 14 §5.3).

    ``signal_ids`` is the multi-selected set; ``status`` is the single target applied to
    each. The list is length-bounded at the schema layer (≥ 1, ≤
    :data:`~signals.services.MAX_BULK_STATUS_BATCH`) so an empty or over-large request is
    a 422 (request validation) before the service runs. ``pushed`` is excluded from the
    settable Literal — it is owned by the K-epic CRM-push flow, never this human PATCH.
    """

    model_config = ConfigDict(extra="forbid")

    signal_ids: Annotated[
        list[uuid.UUID],
        Field(min_length=1, max_length=services.MAX_BULK_STATUS_BATCH),
    ]
    status: SettableStatus


class BulkStatusSkipRead(BaseModel):
    """One signal that could not be transitioned in a bulk request (G3)."""

    model_config = ConfigDict(extra="forbid")

    signal_id: uuid.UUID
    # Stable reason code: ``not_in_workspace`` | ``illegal_transition``.
    reason: str
    # Current status when known (None when the signal has no score row here).
    current: str | None = None


class BulkStatusChangeRead(BaseModel):
    """The per-item outcome of a G3 bulk status transition (doc 14 §5.3)."""

    model_config = ConfigDict(extra="forbid")

    status: str
    succeeded: list[uuid.UUID]
    skipped: list[BulkStatusSkipRead]


@router.post(
    "/bulk-status",
    response_model=BulkStatusChangeRead,
    summary="Bulk-transition signals' per-workspace status (G3)",
)
async def change_status_bulk(
    ctx: RequireMember,
    session: SessionDep,
    body: Annotated[BulkStatusChangeIn, Body()],
) -> BulkStatusChangeRead | JSONResponse:
    """Bulk-transition many signals' ``signals_workspace_score.status`` (G3, mass actions).

    The multi-select triage action (mass dismiss / mass pin, doc 14 §5.3). Applies one
    target ``status`` to every selected signal's score row, reusing the same transition
    graph as the G4 single PATCH. **Resilient**: a signal with no score row in this
    workspace, or whose current status forbids the move, is reported in ``skipped`` (with
    a reason) rather than failing the whole batch — the legal moves still apply.

    Member-gated (``RequireMember``: viewers cannot mutate workspace data, B7) and
    workspace-scoped — rows are read by ``(workspace_id, signal_id IN …)`` from the
    resolved :class:`WorkspaceContext` (never a body/query param), so one workspace can
    never transition another's rows (doc 08 §1.4). The batch is bounded
    (``services.MAX_BULK_STATUS_BATCH``); the request schema rejects an over-large or
    empty selection as a 422 before the service runs.

    The ``pushed`` state is not settable here (K-epic-owned, doc 14 §5.3); the schema's
    ``Literal`` rejects it as a 422.

    Emits one :data:`~civicsignals_api.events.SIGNAL_STATUS_CHANGED` per successfully
    transitioned signal so B9's audit listener records each change (best-effort; a failed
    audit never fails the action). Idempotent no-ops (a row already at the target) count
    as succeeded but emit no audit event.
    """
    try:
        result = await services.change_workspace_score_status_bulk(
            session,
            workspace_id=ctx.workspace.id,
            signal_ids=body.signal_ids,
            target_status=body.status,
        )
    except services.BulkStatusBatchTooLargeError as exc:  # pragma: no cover - schema caps first
        return _problem(
            422,
            "Bulk status batch too large",
            f"Requested {exc.count} signals; the per-request limit is {exc.limit}.",
        )
    await session.commit()

    # Best-effort audit (B9 persists), one event per actually-transitioned signal. A
    # broken audit sink must not fail the action. ``result.moved`` excludes idempotent
    # no-ops (already-at-target rows land in ``succeeded`` but not ``moved``).
    for signal_id in result.moved:
        try:
            await events.publish(
                events.SIGNAL_STATUS_CHANGED,
                {
                    "signal_id": str(signal_id),
                    "workspace_id": str(ctx.workspace.id),
                    "user_id": str(ctx.user.id),
                    "status": result.target_status,
                },
            )
        except Exception:  # pragma: no cover - defensive; audit is fire-and-forget
            log.warning("signal_status_changed_event_failed", signal_id=str(signal_id))

    return BulkStatusChangeRead(
        status=result.target_status,
        succeeded=result.succeeded,
        skipped=[
            BulkStatusSkipRead(signal_id=s.signal_id, reason=s.reason, current=s.current)
            for s in result.skipped
        ],
    )


# ---------------------------------------------------------------------------
# F5 feedback endpoint — workspace-scoped POST/DELETE of the user's verdict.
#
# Registered before the bare ``/{signal_id}`` (below). ``/{signal_id}/feedback`` is a
# deeper path so FastAPI matches it first, but ordering it here keeps the convention
# (more-specific routes precede the catch-all) explicit.
# ---------------------------------------------------------------------------


# The three feedback verdicts (F5, doc 14 §12). A ``Literal`` so an unknown kind is a
# 422 (request validation) before it reaches the service.
FeedbackKind = Literal["relevant", "not_relevant", "wrong_extraction"]


class FeedbackIn(BaseModel):
    """Request body for the F5 feedback verdict (doc 14 §12)."""

    model_config = ConfigDict(extra="forbid")

    kind: FeedbackKind


class FeedbackRead(BaseModel):
    """The recorded feedback verdict for one (workspace, signal) pair (F5)."""

    model_config = ConfigDict(extra="forbid")

    signal_id: uuid.UUID
    kind: str


@router.post(
    "/{signal_id}/feedback",
    response_model=FeedbackRead,
    summary="Record / change a signal's relevance feedback (F5)",
)
async def submit_feedback(
    signal_id: uuid.UUID,
    ctx: RequireMember,
    session: SessionDep,
    body: Annotated[FeedbackIn, Body()],
) -> FeedbackRead | JSONResponse:
    """Record (or change) the calling user's feedback on a signal (F5, doc 14 §12).

    Captures one of ``relevant`` / ``not_relevant`` / ``wrong_extraction`` for the
    ``(workspace, signal, user)`` triple — upserting so re-submitting a different kind
    changes the verdict. ``relevant`` / ``not_relevant`` aggregate into a **bounded**
    per-workspace nudge to the signal type's weight that re-weights *subsequent* scores
    (doc 14 §12, applied via ``services.scoring_config_for_workspace`` in the F3 fan-out
    + F6 backfill). ``wrong_extraction`` is an extraction-quality flag and does **not**
    alter scoring — it is recorded + surfaced for review (QA-7 / E-epic seam).

    Member-gated (``RequireMember``: viewers cannot mutate workspace data, B7) and
    workspace-scoped — the workspace + user come from the resolved
    :class:`WorkspaceContext` (never a body/query param), so one workspace/user can
    never write another's feedback (doc 14 §12, doc 08 §1.4). RFC 7807 errors.

    Note: this records the verdict and nudges *future* scoring; it does not
    synchronously re-score the existing feed (a full re-score is the F6 backfill's job).
    """
    try:
        row = await services.set_signal_feedback(
            session,
            workspace_id=ctx.workspace.id,
            signal_id=signal_id,
            user_id=ctx.user.id,
            kind=body.kind,
        )
    except services.FeedbackKindError as exc:  # pragma: no cover - schema rejects first
        return _problem(422, "Invalid feedback kind", str(exc))
    await session.commit()
    return FeedbackRead(signal_id=signal_id, kind=row.kind)


@router.delete(
    "/{signal_id}/feedback",
    response_model=FeedbackRead,
    summary="Retract a signal's relevance feedback (F5)",
)
async def retract_feedback(
    signal_id: uuid.UUID,
    ctx: RequireMember,
    session: SessionDep,
) -> FeedbackRead | JSONResponse:
    """Retract the calling user's feedback verdict on a signal (F5, doc 14 §12).

    Deletes the ``(workspace, signal, user)`` verdict so it no longer contributes to
    the per-workspace re-weighting. Idempotent: a 404 is returned only when there was
    no verdict to retract. Member-gated + workspace/user-scoped (B7, doc 14 §12).
    """
    removed = await services.clear_signal_feedback(
        session,
        workspace_id=ctx.workspace.id,
        signal_id=signal_id,
        user_id=ctx.user.id,
    )
    if not removed:
        return _problem(
            404,
            "No feedback to retract",
            f"You have no feedback recorded for signal {signal_id}.",
        )
    await session.commit()
    return FeedbackRead(signal_id=signal_id, kind="")


# ---------------------------------------------------------------------------
# Parameterised signal endpoint — MUST be last so static prefixes above win.
# ---------------------------------------------------------------------------


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
