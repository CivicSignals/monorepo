"""HTTP endpoints for the recipes module, mounted under `/api/v1/recipes`.

D5 ships the staff **recipe preview** endpoint (``POST /recipes/preview``): a
recipe (by id or inline YAML) + a sample input (pasted HTML or a URL) is
dry-run through the runner, returning the extracted records, per-field results,
and the ``degraded``/diagnostic info an author needs (doc 18 §3).

E12 adds two **read-only scorecard endpoints** (workspace-authenticated, any
member):

- ``GET /recipes/scorecards`` — list all recipes' scorecards, cursor-paginated,
  optional ``?health=`` filter.
- ``GET /recipes/scorecards/{recipe_id}`` — single recipe scorecard.

QA-7 adds **quality sampling endpoints** (workspace-authenticated, admin role):

- ``GET /recipes/quality-samples`` — list pending quality samples, optional
  ``?recipe_id=`` / ``?window=`` filters.
- ``POST /recipes/quality-samples/{sample_id}/judgements`` — record per-field
  accuracy for one sample (admin only).
- ``GET /recipes/quality-accuracy`` — per-recipe accuracy rollup (read-only,
  optional ``?window=`` filter).
- ``GET /recipes/quality-accuracy/{recipe_id}`` — single recipe accuracy
  rollup (read-only).

Errors use RFC 7807 ``application/problem+json`` (doc 06 §5). The preview
endpoint is **staff-only** via a deliberate stub (``staff_problem``) — real
workspace RBAC is TODO B7; see the stub for the seam.  Scorecard endpoints use
the standard ``require_workspace`` / ``RequireViewer`` dependency (B5/B7).
Quality sampling endpoints require ``RequireAdmin`` (workspace admin or owner).
"""

from __future__ import annotations

import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import get_settings
from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import RequireAdmin, RequireViewer
from civicsignals_api.problems import ProblemException

from . import quality_sampling as qs
from . import scorecard as scorecard_svc
from . import services
from .models import QualitySample
from .schemas import (
    PreviewRequest,
    PreviewResult,
    QualitySampleListOut,
    QualitySampleOut,
    RecipeAccuracyListOut,
    RecipeAccuracyOut,
    RecipeScorecardOut,
    RecordFieldAccuracyIn,
    ScorecardHealthOut,
    ScorecardPageOut,
)

router = APIRouter(prefix="/recipes", tags=["recipes"])

PROBLEM_JSON = "application/problem+json"
SessionDep = Annotated[AsyncSession, Depends(get_session)]
CursorQuery = Annotated[str | None, Query(description="Opaque pagination cursor.")]
LimitQuery = Annotated[int, Query(ge=1, le=scorecard_svc.MAX_LIMIT)]


def _problem(status: int, title: str, detail: str) -> JSONResponse:
    """Build an RFC 7807 ``application/problem+json`` response (doc 06 §5)."""
    return JSONResponse(
        status_code=status,
        media_type=PROBLEM_JSON,
        content={"type": "about:blank", "title": title, "status": status, "detail": detail},
    )


def staff_problem(x_staff_token: str | None) -> JSONResponse | None:
    """Staff-only gate for recipe authoring endpoints (TODO D5).

    Returns a 403 problem response if the caller is not staff, else ``None``.

    TODO B7: replace this with real workspace RBAC (admin/staff role check on the
    authenticated principal) once auth (B1/B5) and roles (B7) land. Until then we
    fail closed outside development and accept a shared ``X-Staff-Token`` header
    that must match ``RECIPE_PREVIEW_STAFF_TOKEN``. The endpoint never touches
    customer data — it only runs an author-supplied recipe against author-supplied
    input — so a shared-token stub is an acceptable interim guard.
    """
    settings = get_settings()
    expected = settings.recipe_preview_staff_token
    if expected is None:
        if settings.environment == "development":
            return None  # dev convenience: open locally
        return _problem(
            403,
            "Forbidden",
            "recipe preview is disabled (set RECIPE_PREVIEW_STAFF_TOKEN to enable)",
        )
    # Constant-time compare to reduce timing side-channels about token content.
    if not secrets.compare_digest(x_staff_token or "", expected):
        return _problem(403, "Forbidden", "a valid X-Staff-Token header is required")
    return None


@router.post(
    "/preview",
    response_model=PreviewResult,
    summary="Dry-run a recipe against a sample input (staff only)",
    responses={
        400: {"description": "Bad request", "content": {PROBLEM_JSON: {}}},
        403: {"description": "Forbidden (staff only)", "content": {PROBLEM_JSON: {}}},
        404: {"description": "Recipe not found", "content": {PROBLEM_JSON: {}}},
        502: {"description": "Upstream fetch failed", "content": {PROBLEM_JSON: {}}},
    },
)
def preview_recipe_endpoint(
    request: PreviewRequest,
    x_staff_token: Annotated[str | None, Header(alias="X-Staff-Token")] = None,
) -> PreviewResult | JSONResponse:
    """Run a recipe preview and return field-level results + diagnostics.

    A required-field miss is *not* an error here: it comes back in
    :class:`PreviewResult` with ``ok=False`` and ``error`` set, so the staff UI
    can render the partial extraction. Only request/recipe/posture problems map
    to RFC 7807 responses.
    """
    forbidden = staff_problem(x_staff_token)
    if forbidden is not None:
        return forbidden

    try:
        return services.preview_recipe(request)
    except services.RecipeValidationError as exc:
        return _problem(400, "Invalid recipe", "; ".join(exc.messages))
    except services.RecipeNotFoundError as exc:
        return _problem(404, "Recipe not found", str(exc))
    except services.RobotsDisallowedError as exc:
        return _problem(502, "Fetch disallowed by robots.txt", str(exc))
    except services.FetchFailedError as exc:
        return _problem(502, "Upstream fetch failed", str(exc))
    except services.RecipeError as exc:
        # Remaining recipe-domain errors (bad YAML, bad one-of input) are the
        # caller's mistake: 400. The 404/502 cases are caught above by *type*, so
        # routing never depends on message text.
        return _problem(400, "Bad request", str(exc))


# ---------------------------------------------------------------------------
# E12 — Recipe scorecard / quality dashboard endpoints (read-only)
# ---------------------------------------------------------------------------


def _scorecard_out(card: scorecard_svc.RecipeScorecard) -> RecipeScorecardOut:
    """Map an internal :class:`RecipeScorecard` to the API response schema."""
    return RecipeScorecardOut(
        recipe_id=card.recipe_id,
        health=ScorecardHealthOut(card.health.value),
        drift_paused=card.drift_paused,
        paused_reason=card.paused_reason,
        paused_at=card.paused_at,
        drift_issue_url=card.drift_issue_url,
        last_run_at=card.last_run_at,
        window_24h=card.window_24h,
        window_7d=card.window_7d,
        computed_at=card.computed_at,
    )


@router.get(
    "/scorecards",
    response_model=ScorecardPageOut,
    summary="List per-recipe quality scorecards (workspace-scoped, read-only)",
    responses={
        400: {"description": "Bad request (invalid cursor)", "content": {PROBLEM_JSON: {}}},
        401: {"description": "Unauthorized", "content": {PROBLEM_JSON: {}}},
        404: {"description": "Workspace not found", "content": {PROBLEM_JSON: {}}},
    },
)
async def list_scorecards_endpoint(
    session: SessionDep,
    _ctx: RequireViewer,
    cursor: CursorQuery = None,
    limit: LimitQuery = scorecard_svc.DEFAULT_LIMIT,
    health: Annotated[
        ScorecardHealthOut | None,
        Query(description="Filter by health status (healthy/degraded/paused/unknown)."),
    ] = None,
) -> ScorecardPageOut:
    """Return a cursor-paginated list of recipe scorecards.

    Each scorecard summarises one recipe's quality metrics from the last 24h and
    7d rolling windows (E7 ``RunMetric``/``DriftState`` data).  Ordered
    alphabetically by recipe id; pass ``?cursor=<next_cursor>`` for subsequent
    pages.  Filter by ``?health=degraded`` (or ``paused``/``healthy``/``unknown``)
    to surface only the recipes that need attention.

    Workspace membership is required (``X-Workspace-Id`` header or last-active
    fallback, doc 08 §1.4). Read-only — viewer role and above.
    """
    try:
        health_filter = scorecard_svc.ScorecardHealth(health.value) if health is not None else None
        page = await scorecard_svc.list_scorecards(
            session,
            health=health_filter,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise ProblemException(
            status=status.HTTP_400_BAD_REQUEST,
            code="bad_request",
            title="Bad request",
            detail="Invalid cursor.",
        ) from exc
    return ScorecardPageOut(
        items=[_scorecard_out(card) for card in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/scorecards/{recipe_id}",
    response_model=RecipeScorecardOut,
    summary="Get a single recipe's quality scorecard (workspace-scoped, read-only)",
    responses={
        401: {"description": "Unauthorized", "content": {PROBLEM_JSON: {}}},
        404: {"description": "Workspace not found", "content": {PROBLEM_JSON: {}}},
    },
)
async def get_scorecard_endpoint(
    recipe_id: str,
    session: SessionDep,
    _ctx: RequireViewer,
) -> RecipeScorecardOut:
    """Return the quality scorecard for one recipe.

    A scorecard is returned even when the recipe has no recorded runs yet
    (health is ``unknown``, all metric counts are zero).  The ``recipe_id`` is
    the recipe slug string (same as in ``RunMetric``/``DriftState``).

    Workspace membership is required. Read-only — viewer role and above.
    """
    card = await scorecard_svc.get_scorecard(session, recipe_id)
    return _scorecard_out(card)


# ---------------------------------------------------------------------------
# QA-7 — Extraction quality sampling endpoints (admin-only)
# ---------------------------------------------------------------------------


def _sample_out(sample: QualitySample) -> QualitySampleOut:
    """Map a :class:`QualitySample` ORM row to the API response shape."""
    return QualitySampleOut(
        id=str(sample.id),
        signal_id=str(sample.signal_id),
        recipe_id=sample.recipe_id,
        signal_type=sample.signal_type,
        field_snapshot=sample.field_snapshot or {},
        sample_window=sample.sample_window,
        sampled_at=sample.sampled_at,
        field_judgements=sample.field_judgements,
        overall_accuracy=sample.overall_accuracy,
        reviewer=sample.reviewer,
        reviewed_at=sample.reviewed_at,
    )


def _accuracy_out(acc: qs.RecipeAccuracy) -> RecipeAccuracyOut:
    """Map a :class:`RecipeAccuracy` to the API response shape."""
    return RecipeAccuracyOut(
        recipe_id=acc.recipe_id,
        sample_count=acc.sample_count,
        reviewed_count=acc.reviewed_count,
        overall_accuracy=acc.overall_accuracy,
        field_accuracy=acc.field_accuracy,
        window=acc.window,
    )


@router.get(
    "/quality-samples",
    response_model=QualitySampleListOut,
    summary="List pending extraction quality samples (admin only)",
    responses={
        401: {"description": "Unauthorized", "content": {PROBLEM_JSON: {}}},
        403: {"description": "Forbidden — admin role required", "content": {PROBLEM_JSON: {}}},
        404: {"description": "Workspace not found", "content": {PROBLEM_JSON: {}}},
    },
)
async def list_quality_samples_endpoint(
    session: SessionDep,
    _ctx: RequireAdmin,
    recipe_id: Annotated[str | None, Query(description="Filter by recipe id slug.")] = None,
    window: Annotated[
        str | None,
        Query(description="Filter by ISO-week window label (e.g. '2026-W21')."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QualitySampleListOut:
    """List quality samples that are pending review.

    Only samples whose ``reviewed_at`` is ``None`` are returned. Optionally
    filter by ``recipe_id`` and/or ``window`` (ISO-week label, e.g. ``2026-W21``).

    Requires admin role or above (workspace admin + owner).
    """
    samples = await qs.list_pending_samples(
        session,
        recipe_id=recipe_id,
        window=window,
        limit=limit,
        offset=offset,
    )
    return QualitySampleListOut(
        items=[_sample_out(s) for s in samples],
        total=len(samples),
    )


@router.post(
    "/quality-samples/{sample_id}/judgements",
    response_model=QualitySampleOut,
    summary="Record per-field accuracy judgements for a quality sample (admin only)",
    responses={
        400: {"description": "Invalid verdict value", "content": {PROBLEM_JSON: {}}},
        401: {"description": "Unauthorized", "content": {PROBLEM_JSON: {}}},
        403: {"description": "Forbidden — admin role required", "content": {PROBLEM_JSON: {}}},
        404: {"description": "Sample not found", "content": {PROBLEM_JSON: {}}},
    },
)
async def record_field_accuracy_endpoint(
    sample_id: uuid.UUID,
    body: RecordFieldAccuracyIn,
    session: SessionDep,
    _ctx: RequireAdmin,
) -> QualitySampleOut | JSONResponse:
    """Record per-field correct/incorrect/unknown judgements for one quality sample.

    ``field_judgements`` maps field name to ``"correct" | "incorrect" | "unknown"``.
    Each call merges the supplied judgements with any previously recorded ones,
    so reviewers may update a sample incrementally. ``overall_accuracy`` is
    recomputed from the merged judgement map (fraction correct/(correct+incorrect)
    ignoring unknown fields).

    Requires admin role or above.
    """
    try:
        async with session.begin():
            updated = await qs.record_field_accuracy(
                session,
                sample_id,
                field_judgements=body.field_judgements,
                reviewer=body.reviewer,
            )
        return _sample_out(updated)
    except qs.InvalidVerdict as exc:
        return _problem(400, "Invalid verdict", str(exc))
    except qs.SampleNotFound as exc:
        raise ProblemException(
            status=status.HTTP_404_NOT_FOUND,
            code="not_found",
            title="Sample not found",
            detail=f"Quality sample {sample_id} not found.",
        ) from exc


@router.get(
    "/quality-accuracy",
    response_model=RecipeAccuracyListOut,
    summary="Per-recipe extraction accuracy rollup (admin only)",
    responses={
        401: {"description": "Unauthorized", "content": {PROBLEM_JSON: {}}},
        403: {"description": "Forbidden — admin role required", "content": {PROBLEM_JSON: {}}},
        404: {"description": "Workspace not found", "content": {PROBLEM_JSON: {}}},
    },
)
async def list_recipe_accuracy_endpoint(
    session: SessionDep,
    _ctx: RequireAdmin,
    window: Annotated[
        str | None,
        Query(description="Scope rollup to a single ISO-week window label."),
    ] = None,
) -> RecipeAccuracyListOut:
    """Aggregate extraction accuracy by recipe across all recorded quality samples.

    For each recipe that has at least one quality sample, returns the overall
    accuracy (mean ``overall_accuracy`` of reviewed rows), per-field accuracy
    percentages, sample count, and reviewed count. Optionally scoped to a single
    ``window`` (ISO-week label).

    Requires admin role or above. Read-only.
    """
    accuracies = await qs.list_recipe_accuracy(session, window=window)
    return RecipeAccuracyListOut(items=[_accuracy_out(a) for a in accuracies])


@router.get(
    "/quality-accuracy/{recipe_id}",
    response_model=RecipeAccuracyOut,
    summary="Extraction accuracy rollup for one recipe (admin only)",
    responses={
        401: {"description": "Unauthorized", "content": {PROBLEM_JSON: {}}},
        403: {"description": "Forbidden — admin role required", "content": {PROBLEM_JSON: {}}},
        404: {"description": "Workspace not found", "content": {PROBLEM_JSON: {}}},
    },
)
async def get_recipe_accuracy_endpoint(
    recipe_id: str,
    session: SessionDep,
    _ctx: RequireAdmin,
    window: Annotated[
        str | None,
        Query(description="Scope rollup to a single ISO-week window label."),
    ] = None,
) -> RecipeAccuracyOut:
    """Return extraction accuracy rollup for a single recipe.

    Returns a rollup even when no samples exist (``sample_count`` = 0,
    ``overall_accuracy`` = ``None``). Optionally scoped to a single ``window``
    (ISO-week label).

    Requires admin role or above. Read-only.
    """
    acc = await qs.get_recipe_accuracy(session, recipe_id, window=window)
    return _accuracy_out(acc)
