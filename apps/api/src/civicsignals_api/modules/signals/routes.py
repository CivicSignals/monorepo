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
  GET  /signals/{id}/public                  — public narrowed signal projection (P2, public types only)
  GET  /signals/{id}/sources                 — public source citations for a signal (P2, public types only)
  GET  /signals/{id}                         — get one signal (full internal read)

Route ordering note: ``/feed``, ``/fuzzy-reviews`` and their sub-paths MUST be
registered before ``/{signal_id}`` (the parameterised catch-all) so that FastAPI's
routing evaluates the static prefix first. Moving ``/{signal_id}`` to the end of
the file preserves this invariant regardless of how many static-prefix endpoints
are added later.

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
from civicsignals_api.modules.auth.dependencies import RequireAdmin, RequireViewer

from . import services
from .schemas import PublicSignalRead, SignalPage, SignalRead, SignalSourcesRead
from .services import WorkspaceFeedPage

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

    TODO G3: bulk actions (dismiss/pin batch) build on this feed.
    TODO G4: status transitions (dismiss/pin/push single) will be PATCH endpoints.
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
# Public signal read + source citations (P2) — read-only, unauthenticated.
#
# Registered before /{signal_id} so the static ``/public`` / ``/sources`` suffixes
# are matched as sub-resources of the signal id (mirrors entities'
# ``/{entity_id}/children``).
#
# BOTH gate on ``PUBLIC_SIGNAL_TYPES`` (doc 13 §4.1, §4.6): a non-public (paid-tier)
# signal 404s server-side so the public surface cannot be scraped by id. The full
# internal ``SignalRead`` stays on the authenticated ``GET /{signal_id}`` below.
# ---------------------------------------------------------------------------


@router.get(
    "/{signal_id}/public",
    response_model=PublicSignalRead,
    summary="Public signal projection for the /s/{id} page (P2)",
)
async def get_public_signal(
    signal_id: uuid.UUID,
    session: SessionDep,
) -> PublicSignalRead | JSONResponse:
    """Public, unauthenticated, narrowed read of a signal (P2; doc 13 §4.1, §4.6).

    Powers the public ``/s/{id}`` page. Returns only public-safe fields (id, type,
    title, summary, public entity name, occurred/observed dates) — never the internal
    ``content_hash`` / ``raw_document_ids`` / ``confidence`` / ``status`` / ``details``
    that the full ``SignalRead`` carries (doc 13 §4.2: depth is paid). 404s when the
    signal does not exist or its type is not in the public allowlist, so a paid-tier
    signal cannot be scraped by id.
    """
    result = await services.get_public_signal(session, signal_id)
    if result is None:
        return _problem(404, "Signal not found", f"No signal with id {signal_id}.")
    return result


@router.get(
    "/{signal_id}/sources",
    response_model=SignalSourcesRead,
    summary="Public source citations for a signal (P2)",
)
async def get_signal_sources(
    signal_id: uuid.UUID,
    session: SessionDep,
) -> SignalSourcesRead | JSONResponse:
    """Public, unauthenticated source citations for a signal (P2; doc 07 §3).

    Signals are global (like the entity directory), so this read endpoint requires
    no ``X-Workspace-Id`` and no auth — it powers the public ``/s/{id}`` signal page,
    which cites where each fact came from. Returns the public-safe provenance of every
    corroborating ``ingestion_raw_document`` (source URL + recipe + fetch time); the
    S3 key and internal metadata are never exposed. 404s when the signal does not
    exist or its type is not in :data:`~signals.schemas.PUBLIC_SIGNAL_TYPES`
    (doc 13 §4.1, §4.6) so a paid-tier signal cannot be scraped by id.
    """
    result = await services.get_signal_sources(session, signal_id)
    if result is None:
        return _problem(404, "Signal not found", f"No signal with id {signal_id}.")
    return result


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
