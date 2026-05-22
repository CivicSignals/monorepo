"""HTTP endpoints for the foia module, mounted under ``/api/v1/foia``.

Template endpoints (M1)
-----------------------
``GET  /api/v1/foia/templates``         — list all templates (global reference data).
``GET  /api/v1/foia/templates/{juri}``  — get one template by jurisdiction code.
``POST /api/v1/foia/templates/{juri}/render`` — render a template with context vars.

FOIA request endpoints (M2)
----------------------------
``POST   /api/v1/foia/requests``               — create a new request (workspace-scoped).
``GET    /api/v1/foia/requests``               — list requests (cursor-paginated).
``GET    /api/v1/foia/requests/{id}``          — get one request.
``PATCH  /api/v1/foia/requests/{id}``          — update a draft request.
``POST   /api/v1/foia/requests/{id}/transition`` — advance the state machine.
``GET    /api/v1/foia/requests/{id}/events``   — status-transition history.

FOIA attachment endpoints (M3)
-------------------------------
``POST  /api/v1/foia/requests/{id}/attachments``          — upload a response PDF.
``GET   /api/v1/foia/requests/{id}/attachments``          — list attachments (cursor-paginated).
``GET   /api/v1/foia/requests/{id}/attachments/{att_id}`` — get one attachment.
``GET   /api/v1/foia/requests/{id}/attachments/{att_id}/signals`` — signals from attachment.

FOIA reminder config endpoints (M5)
-------------------------------------
``GET   /api/v1/foia/requests/{id}/reminder``  — get reminder config.
``PATCH /api/v1/foia/requests/{id}/reminder``  — update reminder config.

**Workspace scoping (M2):** FOIA request endpoints require the
``X-Workspace-Id`` header (or a ``last_active_workspace_id`` fallback) via
:data:`auth.dependencies.CurrentWorkspace`. Template endpoints are
intentionally unauthenticated (global reference data).

**Auth:** Template read endpoints are intentionally unauthenticated in M1 so
that the template picker can be displayed before a user selects a workspace.
This may be tightened to "authenticated, any workspace" in a future security
review.

Errors follow RFC 7807 ``application/problem+json`` via
:mod:`civicsignals_api.problems` (doc 06 §5).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import CurrentWorkspace
from civicsignals_api.problems import ProblemException

from . import services
from .models import ALLOWED_TRANSITIONS, FoiaRequestStatus
from .schemas import (
    FoiaAttachmentPage,
    FoiaAttachmentRead,
    FoiaAttachmentSignalRef,
    FoiaReminderConfigRead,
    FoiaReminderConfigUpdate,
    FoiaRequestCreate,
    FoiaRequestEventRead,
    FoiaRequestPage,
    FoiaRequestRead,
    FoiaRequestTransition,
    FoiaRequestUpdate,
    FoiaTemplateList,
    FoiaTemplateRead,
    FoiaTemplateRenderRequest,
    FoiaTemplateRenderResponse,
)

router = APIRouter(prefix="/foia", tags=["foia"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _template_not_found(jurisdiction: str) -> ProblemException:
    return ProblemException(
        status=404,
        code="foia_template_not_found",
        title="FOIA template not found",
        detail=f"No template exists for jurisdiction {jurisdiction!r}.",
    )


def _request_not_found(request_id: uuid.UUID) -> ProblemException:
    return ProblemException(
        status=404,
        code="foia_request_not_found",
        title="FOIA request not found",
        detail=f"FOIA request {request_id} not found in the current workspace.",
    )


def _entity_not_found(entity_id: uuid.UUID) -> ProblemException:
    return ProblemException(
        status=422,
        code="foia_entity_not_found",
        title="Entity not found",
        detail=f"Entity {entity_id} does not exist in the entity directory.",
        errors=[{"field": "entity_id", "code": "not_found", "message": "Entity not found."}],
    )


# ---------------------------------------------------------------------------
# Template endpoints (M1) — no workspace required
# ---------------------------------------------------------------------------


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
        raise _template_not_found(jurisdiction) from None
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
        raise _template_not_found(jurisdiction) from None
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


# ---------------------------------------------------------------------------
# FOIA request endpoints (M2) — workspace-scoped
# ---------------------------------------------------------------------------


@router.post(
    "/requests",
    response_model=FoiaRequestRead,
    status_code=201,
    summary="Create a FOIA request",
    description=(
        "Create a new FOIA / public-records request in the current workspace. "
        "The request can be created from a template (supply ``jurisdiction`` + "
        "``template_context``) or freeform (supply ``body`` directly). "
        "The request starts in ``draft`` status."
    ),
)
async def create_request(
    body: FoiaRequestCreate,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> FoiaRequestRead:
    """Create a new FOIA request (workspace-scoped)."""
    try:
        req = await services.create_request(
            session,
            workspace_id=ctx.workspace_id,
            created_by=ctx.user.id,
            entity_id=body.entity_id,
            subject=body.subject,
            body=body.body,
            jurisdiction=body.jurisdiction,
            template_context=body.template_context,
            submission_method=body.submission_method.value,
            submission_target=body.submission_target,
        )
    except services.FoiaEntityNotFoundError as exc:
        raise _entity_not_found(exc.entity_id) from exc
    except services.TemplateNotFoundError as exc:
        juri = str(exc.args[0]) if exc.args else "unknown"
        raise _template_not_found(juri) from exc
    except services.MissingPlaceholderError as exc:
        raise ProblemException(
            status=422,
            code="foia_template_missing_placeholders",
            title="Missing required placeholders",
            detail=(
                f"Template {exc.jurisdiction!r} requires placeholder(s) "
                f"that were not supplied or were empty: {exc.missing!r}"
            ),
            errors=[
                {
                    "field": f"template_context.{p}",
                    "code": "required",
                    "message": "This placeholder is required.",
                }
                for p in exc.missing
            ],
        ) from exc
    except ValueError as exc:
        raise ProblemException(
            status=422,
            code="foia_invalid_request",
            title="Invalid FOIA request",
            detail=str(exc),
        ) from exc
    await session.commit()
    await session.refresh(req)
    return FoiaRequestRead.model_validate(req)


@router.get(
    "/requests",
    response_model=FoiaRequestPage,
    summary="List FOIA requests",
    description=(
        "Return a cursor-paginated list of FOIA requests in the current workspace. "
        "Optionally filter by ``status`` or ``entity_id``. "
        "Results are ordered by creation time ascending (oldest first) via UUID v7 keyset pagination. "
        "Use ``cursor`` from the previous response to fetch the next page."
    ),
)
async def list_requests(
    ctx: CurrentWorkspace,
    session: SessionDep,
    status: Annotated[str | None, Query(description="Filter by status.")] = None,
    entity_id: Annotated[uuid.UUID | None, Query(description="Filter by target entity.")] = None,
    cursor: Annotated[str | None, Query(description="Pagination cursor.")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 25,
) -> FoiaRequestPage:
    """List FOIA requests for the current workspace (cursor-paginated)."""
    # Validate status filter value if provided.
    if status is not None:
        try:
            FoiaRequestStatus(status)
        except ValueError:
            valid = [s.value for s in FoiaRequestStatus]
            raise ProblemException(
                status=422,
                code="foia_invalid_status",
                title="Invalid status filter",
                detail=f"Status must be one of {valid!r}; got {status!r}.",
                errors=[
                    {"field": "status", "code": "invalid", "message": f"Must be one of {valid!r}."}
                ],
            ) from None

    try:
        items, next_cursor = await services.list_requests(
            session,
            workspace_id=ctx.workspace_id,
            status=status,
            entity_id=entity_id,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise ProblemException(
            status=400,
            code="foia_invalid_cursor",
            title="Invalid pagination cursor",
            detail=str(exc),
        ) from exc

    return FoiaRequestPage(
        items=[FoiaRequestRead.model_validate(r) for r in items],
        next_cursor=next_cursor,
    )


@router.get(
    "/requests/{request_id}",
    response_model=FoiaRequestRead,
    summary="Get a FOIA request",
    description="Fetch a single FOIA request by id, workspace-scoped.",
)
async def get_request(
    request_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> FoiaRequestRead:
    """Return one FOIA request by id."""
    try:
        req = await services.get_request(
            session, request_id=request_id, workspace_id=ctx.workspace_id
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    return FoiaRequestRead.model_validate(req)


@router.patch(
    "/requests/{request_id}",
    response_model=FoiaRequestRead,
    summary="Update a FOIA request (draft only)",
    description=(
        "Patch a FOIA request. Only ``draft`` requests can be edited; "
        "attempting to update a non-draft request returns a 409 error. "
        "All fields are optional; only supplied fields are updated."
    ),
)
async def update_request(
    request_id: uuid.UUID,
    body: FoiaRequestUpdate,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> FoiaRequestRead:
    """Update a draft FOIA request."""
    try:
        req = await services.update_request(
            session,
            request_id=request_id,
            workspace_id=ctx.workspace_id,
            subject=body.subject,
            body=body.body,
            submission_method=body.submission_method.value if body.submission_method else None,
            submission_target=body.submission_target,
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    except services.FoiaDraftOnlyError as exc:
        raise ProblemException(
            status=409,
            code="foia_not_draft",
            title="FOIA request is not in draft status",
            detail=(
                f"FOIA request {request_id} is in status {exc.current_status!r}. "
                "Only draft requests can be edited."
            ),
        ) from exc
    await session.commit()
    await session.refresh(req)
    return FoiaRequestRead.model_validate(req)


@router.post(
    "/requests/{request_id}/transition",
    response_model=FoiaRequestRead,
    summary="Transition FOIA request status",
    description=(
        "Advance the FOIA request state machine. Allowed paths: "
        "``draft → sent → ack → response``. "
        "Returns a 409 error for illegal transitions. "
        "Transition timestamps (``sent_at``, ``ack_at``, ``response_at``) are "
        "set automatically on the corresponding transition."
    ),
)
async def transition_request(
    request_id: uuid.UUID,
    body: FoiaRequestTransition,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> FoiaRequestRead:
    """Transition a FOIA request to a new status."""
    try:
        req = await services.transition_request(
            session,
            request_id=request_id,
            workspace_id=ctx.workspace_id,
            actor_id=ctx.user.id,
            new_status=body.status,
            response_notes=body.response_notes,
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    except services.FoiaIllegalTransitionError as exc:
        raise ProblemException(
            status=409,
            code="foia_illegal_transition",
            title="Illegal status transition",
            detail=(
                f"Cannot transition FOIA request {request_id} from "
                f"{exc.from_status!r} to {exc.to_status!r}. "
                f"Allowed next statuses from {exc.from_status!r}: "
                f"{[s.value for s in ALLOWED_TRANSITIONS.get(FoiaRequestStatus(exc.from_status), set())]!r}."
            ),
        ) from exc
    await session.commit()
    await session.refresh(req)
    return FoiaRequestRead.model_validate(req)


@router.get(
    "/requests/{request_id}/events",
    response_model=list[FoiaRequestEventRead],
    summary="List FOIA request status-transition events",
    description=(
        "Return the full status-transition history for a FOIA request, "
        "ordered by ``occurred_at`` ascending."
    ),
)
async def list_request_events(
    request_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> list[FoiaRequestEventRead]:
    """Return the status-transition event log for a FOIA request."""
    try:
        events = await services.list_request_events(
            session, request_id=request_id, workspace_id=ctx.workspace_id
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    return [FoiaRequestEventRead.model_validate(e) for e in events]


# ---------------------------------------------------------------------------
# M3 — Attachment endpoints (upload + list + signals)
# ---------------------------------------------------------------------------


def _attachment_not_found(attachment_id: uuid.UUID) -> ProblemException:
    return ProblemException(
        status=404,
        code="foia_attachment_not_found",
        title="FOIA attachment not found",
        detail=f"FOIA attachment {attachment_id} not found for this request.",
    )


def _get_storage() -> object:
    """Return the RawDocumentStorage for the current process (D3 seam).

    Imported lazily so the storage (boto3 / S3) is not constructed during
    module import — keeps the API startup fast and lets tests inject a fake.
    """
    from civicsignals_api.modules.ingestion.storage import RawDocumentStorage

    return RawDocumentStorage.from_settings()


@router.post(
    "/requests/{request_id}/attachments",
    response_model=FoiaAttachmentRead,
    status_code=201,
    summary="Upload a FOIA response document",
    description=(
        "Upload a response file (PDF, HTML, DOCX…) to a FOIA request. "
        "The file is stored in content-addressable storage (D3) and the "
        "extraction pipeline is triggered automatically (E1). "
        "If the request is in ``ack`` status it is automatically advanced to ``response``."
    ),
)
async def upload_attachment(
    request_id: uuid.UUID,
    file: UploadFile,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> FoiaAttachmentRead:
    """Upload a response document and enqueue it through the extraction pipeline."""
    content = await file.read()
    filename = file.filename or "upload"
    content_type = file.content_type or "application/octet-stream"

    storage = _get_storage()
    try:
        att = await services.upload_attachment(
            session,
            storage,
            foia_request_id=request_id,
            workspace_id=ctx.workspace_id,
            uploaded_by=ctx.user.id,
            filename=filename,
            content_type=content_type,
            content=content,
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None

    await session.commit()
    return FoiaAttachmentRead.model_validate(att)


@router.get(
    "/requests/{request_id}/attachments",
    response_model=FoiaAttachmentPage,
    summary="List FOIA request attachments",
    description=(
        "Return a cursor-paginated list of uploaded response documents for a FOIA request. "
        "Ordered oldest-first by upload time."
    ),
)
async def list_attachments(
    request_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
    cursor: Annotated[str | None, Query(description="Pagination cursor.")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 25,
) -> FoiaAttachmentPage:
    """List attachments for a FOIA request (cursor-paginated)."""
    try:
        items, next_cursor = await services.list_attachments(
            session,
            foia_request_id=request_id,
            workspace_id=ctx.workspace_id,
            cursor=cursor,
            limit=limit,
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    except ValueError as exc:
        raise ProblemException(
            status=400,
            code="foia_invalid_cursor",
            title="Invalid pagination cursor",
            detail=str(exc),
        ) from exc

    return FoiaAttachmentPage(
        items=[FoiaAttachmentRead.model_validate(a) for a in items],
        next_cursor=next_cursor,
    )


@router.get(
    "/requests/{request_id}/attachments/{attachment_id}",
    response_model=FoiaAttachmentRead,
    summary="Get one FOIA attachment",
    description="Fetch a single attachment by id, workspace-scoped.",
)
async def get_attachment(
    request_id: uuid.UUID,
    attachment_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> FoiaAttachmentRead:
    """Return one FOIA attachment by id."""
    try:
        att = await services.get_attachment(
            session,
            attachment_id=attachment_id,
            foia_request_id=request_id,
            workspace_id=ctx.workspace_id,
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    except services.FoiaAttachmentNotFoundError:
        raise _attachment_not_found(attachment_id) from None
    return FoiaAttachmentRead.model_validate(att)


@router.get(
    "/requests/{request_id}/attachments/{attachment_id}/signals",
    response_model=list[FoiaAttachmentSignalRef],
    summary="List signals extracted from a FOIA attachment",
    description=(
        "Return the signals produced by extraction of this response document. "
        "Signals are linked via the ``raw_document_ids`` array on "
        "``signals_signal`` — the same raw document id used when the PDF was "
        "stored (D3 seam). Ordered newest-first by ``observed_at``."
    ),
)
async def list_attachment_signals(
    request_id: uuid.UUID,
    attachment_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> list[FoiaAttachmentSignalRef]:
    """Return signals linked to a FOIA attachment via raw_document_ids (M3)."""
    try:
        signals = await services.list_attachment_signals(
            session,
            attachment_id=attachment_id,
            foia_request_id=request_id,
            workspace_id=ctx.workspace_id,
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    except services.FoiaAttachmentNotFoundError:
        raise _attachment_not_found(attachment_id) from None
    return [FoiaAttachmentSignalRef.model_validate(s) for s in signals]


# ---------------------------------------------------------------------------
# M5 — Reminder config endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/requests/{request_id}/reminder",
    response_model=FoiaReminderConfigRead,
    summary="Get FOIA reminder configuration",
    description=(
        "Return the current reminder configuration for a FOIA request. "
        "Reminder rules control when nudge emails are sent after a request "
        "has been in ``sent`` status with no acknowledgement or response."
    ),
)
async def get_reminder_config(
    request_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> FoiaReminderConfigRead:
    """Return reminder config for a FOIA request (M5)."""
    try:
        config = await services.get_reminder_config(
            session, request_id=request_id, workspace_id=ctx.workspace_id
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    return FoiaReminderConfigRead(
        reminder_enabled=config.reminder_enabled,
        reminder_days=config.reminder_days,
        reminder_interval_days=config.reminder_interval_days,
        reminder_max=config.reminder_max,
        last_reminded_at=config.last_reminded_at,
        reminder_count=config.reminder_count,
    )


@router.patch(
    "/requests/{request_id}/reminder",
    response_model=FoiaReminderConfigRead,
    summary="Update FOIA reminder configuration",
    description=(
        "Patch the reminder configuration for a FOIA request. "
        "All fields are optional; only supplied non-null values are applied. "
        "``reminder_days`` sets the initial silence threshold (days after sent_at). "
        "``reminder_interval_days`` controls how often to repeat. "
        "``reminder_max`` caps the total number of nudges (0 = unlimited). "
        "Returns a 422 error for invalid values."
    ),
)
async def update_reminder_config(
    request_id: uuid.UUID,
    body: FoiaReminderConfigUpdate,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> FoiaReminderConfigRead:
    """Update reminder config for a FOIA request (M5)."""
    try:
        config = await services.update_reminder_config(
            session,
            request_id=request_id,
            workspace_id=ctx.workspace_id,
            reminder_enabled=body.reminder_enabled,
            reminder_days=body.reminder_days,
            reminder_interval_days=body.reminder_interval_days,
            reminder_max=body.reminder_max,
        )
    except services.FoiaRequestNotFoundError:
        raise _request_not_found(request_id) from None
    except services.FoiaReminderConfigError as exc:
        raise ProblemException(
            status=422,
            code="foia_reminder_config_invalid",
            title="Invalid reminder configuration",
            detail=str(exc),
        ) from exc
    await session.commit()
    return FoiaReminderConfigRead(
        reminder_enabled=config.reminder_enabled,
        reminder_days=config.reminder_days,
        reminder_interval_days=config.reminder_interval_days,
        reminder_max=config.reminder_max,
        last_reminded_at=config.last_reminded_at,
        reminder_count=config.reminder_count,
    )
