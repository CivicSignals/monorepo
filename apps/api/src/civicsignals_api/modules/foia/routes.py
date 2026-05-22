"""HTTP endpoints for the foia module, mounted under `/api/v1/foia`.

Template endpoints (M1)
-----------------------
``GET  /api/v1/foia/templates``         — list all templates (global reference data).
``GET  /api/v1/foia/templates/{juri}``  — get one template by jurisdiction code.
``POST /api/v1/foia/templates/{juri}/render`` — render a template with context vars.

**Workspace scoping:** FOIA templates are *global reference data* analogous to
recipes — they are not workspace-scoped and do not require the ``X-Workspace-Id``
header. Any authenticated user (or anonymous user in a future public API surface)
can enumerate and render templates. Workspace-scoped FOIA *requests* (M2) will
require authentication and the workspace header.

**Auth:** Template read endpoints are intentionally unauthenticated in M1 so that
the template picker can be displayed before a user selects a workspace. This may
be tightened to "authenticated, any workspace" in a future security review.

Errors follow RFC 7807 ``application/problem+json`` via
:mod:`civicsignals_api.problems` (doc 06 §5).
"""

from __future__ import annotations

from fastapi import APIRouter

from civicsignals_api.problems import ProblemException

from . import services
from .schemas import (
    FoiaTemplateList,
    FoiaTemplateRead,
    FoiaTemplateRenderRequest,
    FoiaTemplateRenderResponse,
)

router = APIRouter(prefix="/foia", tags=["foia"])


def _not_found(jurisdiction: str) -> ProblemException:
    return ProblemException(
        status=404,
        code="foia_template_not_found",
        title="FOIA template not found",
        detail=f"No template exists for jurisdiction {jurisdiction!r}.",
    )


@router.get(
    "/templates",
    response_model=FoiaTemplateList,
    summary="List all FOIA templates",
    description=(
        "Return the full library of public-records-request templates. "
        "Templates are global reference data — not workspace-scoped. "
        "All templates are marked **status: draft** pending counsel review."
    ),
)
def list_templates() -> FoiaTemplateList:
    """List all registered FOIA templates."""
    templates = services.list_templates()
    items = [FoiaTemplateRead(**t.as_dict()) for t in templates]
    return FoiaTemplateList(items=items, total=len(items))


@router.get(
    "/templates/{jurisdiction}",
    response_model=FoiaTemplateRead,
    summary="Get one FOIA template",
    description=(
        "Fetch a single FOIA template by jurisdiction code (e.g. ``US-FOIA``, "
        "``CA-PRA``, ``TX-PIA``). Returns 404 if the jurisdiction is not found."
    ),
)
def get_template(jurisdiction: str) -> FoiaTemplateRead:
    """Return one template by jurisdiction code, or raise a 404 problem."""
    try:
        tmpl = services.get_template(jurisdiction)
    except services.TemplateNotFoundError:
        raise _not_found(jurisdiction) from None
    return FoiaTemplateRead(**tmpl.as_dict())


@router.post(
    "/templates/{jurisdiction}/render",
    response_model=FoiaTemplateRenderResponse,
    summary="Render a FOIA template",
    description=(
        "Substitute ``{placeholder}`` tokens in the template body with the values "
        "supplied in ``context``. Returns the rendered body string. "
        "Returns 422 if required placeholders are missing or empty."
    ),
)
def render_template(
    jurisdiction: str,
    body: FoiaTemplateRenderRequest,
) -> FoiaTemplateRenderResponse:
    """Render a template with caller-supplied context."""
    try:
        rendered = services.render_template(jurisdiction, body.context)
    except services.TemplateNotFoundError:
        raise _not_found(jurisdiction) from None
    except services.MissingPlaceholderError as exc:
        raise ProblemException(
            status=422,
            code="foia_template_missing_placeholders",
            title="Missing required placeholders",
            detail=(
                f"Template {jurisdiction!r} requires the following placeholder(s) "
                f"that were not supplied or were empty: {exc.missing!r}"
            ),
            errors=[
                {
                    "field": f"context.{p}",
                    "code": "required",
                    "message": "This placeholder is required.",
                }
                for p in exc.missing
            ],
        ) from exc
    return FoiaTemplateRenderResponse(jurisdiction=jurisdiction, rendered_body=rendered)
