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

Errors use RFC 7807 ``application/problem+json`` (doc 06 §5). The preview
endpoint is **staff-only** via a deliberate stub (``staff_problem``) — real
workspace RBAC is TODO B7; see the stub for the seam.  Scorecard endpoints use
the standard ``require_workspace`` / ``RequireViewer`` dependency (B5/B7).
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import get_settings
from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import RequireViewer
from civicsignals_api.problems import ProblemException

from . import scorecard as scorecard_svc
from . import services
from .schemas import (
    PreviewRequest,
    PreviewResult,
    RecipeScorecardOut,
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
        health_filter = (
            scorecard_svc.ScorecardHealth(health.value) if health is not None else None
        )
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
