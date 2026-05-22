"""HTTP endpoints for the recipes module, mounted under `/api/v1/recipes`.

D5 ships the staff **recipe preview** endpoint (``POST /recipes/preview``): a
recipe (by id or inline YAML) + a sample input (pasted HTML or a URL) is
dry-run through the runner, returning the extracted records, per-field results,
and the ``degraded``/diagnostic info an author needs (doc 18 §3).

Errors use RFC 7807 ``application/problem+json`` (doc 06 §5). The endpoint is
**staff-only** via a deliberate stub (``staff_problem``) — real workspace RBAC
is TODO B7; see the stub for the seam.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from civicsignals_api.config import get_settings

from . import services
from .schemas import PreviewRequest, PreviewResult

router = APIRouter(prefix="/recipes", tags=["recipes"])

PROBLEM_JSON = "application/problem+json"


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
