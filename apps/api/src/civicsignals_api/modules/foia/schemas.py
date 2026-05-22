"""Pydantic request/response shapes for the foia module (doc 06 §3, M2 + M5).

Template responses are **global reference data** — not workspace-scoped —
so those shapes carry no workspace fields. The template list response uses
``items`` + ``total`` (not cursor-paginated) because the library is small
and static (M1).

FOIA request shapes are workspace-scoped and use cursor pagination per
doc 06 §5 (``cursor`` + ``limit`` query params, ``next_cursor`` in responses).

M5 adds reminder config shapes:
- :class:`FoiaReminderConfigRead` — current reminder settings for a request.
- :class:`FoiaReminderConfigUpdate` — PATCH body for the reminder config endpoint.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import FoiaRequestStatus, SubmissionMethod

# ---------------------------------------------------------------------------
# Template shapes (M1, unchanged)
# ---------------------------------------------------------------------------


class FoiaTemplateRead(BaseModel):
    """A FOIA template as exposed on the public API.

    All fields mirror the YAML schema (see foia/templates/README.md).
    ``status`` will always be ``"draft"`` in M1 — templates are pending
    counsel review. Future releases may introduce ``"reviewed"`` status.

    **Workspace scoping:** Templates are global reference data (shared across
    all workspaces, analogous to :class:`recipes_recipe`). No ``workspace_id``
    is present on this response. Authenticated requests are not required for
    the list/get endpoints (treat them as public reference data), though the
    application may choose to restrict access in production.
    """

    model_config = ConfigDict(from_attributes=False)

    jurisdiction: str = Field(
        description="Short jurisdiction code, e.g. 'US-FOIA', 'CA-PRA', 'TX-PIA'."
    )
    jurisdiction_name: str = Field(description="Human-readable name of the law.")
    state: str | None = Field(
        default=None,
        description="Two-letter USPS state code; null for federal templates.",
    )
    statute: str = Field(description="Authoritative statutory citation.")
    deadline_days: int = Field(
        description=(
            "Statutory response deadline in days (calendar or business — "
            "see deadline_note). 0 means no fixed statutory deadline."
        )
    )
    deadline_note: str = Field(
        description="Plain-language explanation of how the deadline is counted and any extensions."
    )
    fee_waiver_language: str = Field(
        description="Boilerplate fee-waiver language for this jurisdiction."
    )
    submission_method_hint: str = Field(
        description="Common submission method(s) for this jurisdiction."
    )
    status: str = Field(description="Template status; 'draft' = pending counsel review.")
    placeholders: list[str] = Field(
        description=(
            "Caller-supplied placeholder keys the client must provide to render this template. "
            "Does NOT include template-owned tokens (e.g. 'fee_waiver_language') that are "
            "injected automatically from the template definition. "
            "Three entries are optional (requester_phone, requester_organization, "
            "records_officer_name) — all others are required."
        )
    )
    body: str = Field(description="Markdown request body with {placeholder} tokens.")


class FoiaTemplateList(BaseModel):
    """All available FOIA templates returned as a single list (no pagination in M1).

    M2 may add cursor pagination if the library grows beyond a few dozen entries.
    """

    model_config = ConfigDict(from_attributes=False)

    items: list[FoiaTemplateRead]
    total: int = Field(description="Total number of templates in the library.")


class FoiaTemplateRenderRequest(BaseModel):
    """Request body for the template render endpoint."""

    model_config = ConfigDict(from_attributes=False)

    context: dict[str, str] = Field(
        description=(
            "Key-value map of placeholder values. Required placeholders (all entries "
            "in the template's 'placeholders' list except requester_phone, "
            "requester_organization, and records_officer_name) must be present and "
            "non-empty, or a 422 error is returned. Optional placeholders default to "
            "empty string when omitted. Template-owned tokens (e.g. fee_waiver_language) "
            "are injected automatically and must NOT be supplied here."
        )
    )


class FoiaTemplateRenderResponse(BaseModel):
    """Response from the template render endpoint."""

    model_config = ConfigDict(from_attributes=False)

    jurisdiction: str
    rendered_body: str = Field(description="Fully rendered request body, ready to send.")


# ---------------------------------------------------------------------------
# FOIA request shapes (M2)
# ---------------------------------------------------------------------------


class FoiaRequestCreate(BaseModel):
    """Request body for ``POST /api/v1/foia/requests`` (create a new request).

    The request can be created in two ways:
    1. **From a template** — supply ``jurisdiction`` and ``template_context``; the
       service will call ``render_template(jurisdiction, template_context)`` and
       use the result as ``body``.  If ``body`` is also supplied it takes
       precedence (useful for post-render edits before saving).
    2. **Freeform** — supply ``body`` directly without ``jurisdiction``.

    ``entity_id`` is required: a FOIA request must always target a known agency
    in the entity directory (C1). The entity is validated via
    ``entities.services.get_entity`` before the row is inserted.
    """

    model_config = ConfigDict(from_attributes=False)

    entity_id: uuid.UUID = Field(description="Target agency / entity (must exist in directory).")
    subject: str = Field(
        min_length=1,
        max_length=512,
        description="Short description of records requested.",
    )

    # Template-based creation (optional; freeform if absent).
    jurisdiction: str | None = Field(
        default=None,
        description=(
            "Jurisdiction code (e.g. 'CA-PRA') to use for template-based body generation. "
            "Required when template_context is supplied."
        ),
    )
    template_context: dict[str, str] | None = Field(
        default=None,
        description=(
            "Placeholder values passed to render_template when jurisdiction is set. "
            "See the template's 'placeholders' list for required keys."
        ),
    )

    # Body may be supplied directly (freeform) or derived from the template.
    body: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Full request body text. If omitted, jurisdiction + template_context must be "
            "provided and the body is rendered from the template. If both body and "
            "jurisdiction/template_context are provided, body takes precedence."
        ),
    )

    submission_method: SubmissionMethod = Field(
        default=SubmissionMethod.MANUAL,
        description="How the request will be physically submitted (default: manual).",
    )
    submission_target: str | None = Field(
        default=None,
        max_length=1024,
        description="Email address, portal URL, or mailing address for submission.",
    )


class FoiaRequestUpdate(BaseModel):
    """Request body for ``PATCH /api/v1/foia/requests/{id}`` (draft-only edits).

    All fields are optional; only supplied fields are updated.  Raises a 409
    ``foia_not_draft`` error if the request is not in ``draft`` status.
    """

    model_config = ConfigDict(from_attributes=False)

    subject: str | None = Field(default=None, min_length=1, max_length=512)
    body: str | None = Field(default=None, min_length=1)
    submission_method: SubmissionMethod | None = Field(default=None)
    submission_target: str | None = Field(default=None, max_length=1024)


class FoiaRequestTransition(BaseModel):
    """Request body for ``POST /api/v1/foia/requests/{id}/transition``.

    ``status`` is the *target* status.  Allowed paths:
        draft → sent → ack → response

    Illegal transitions (e.g. draft → response) raise a 409
    ``foia_illegal_transition`` RFC 7807 error. ``response_notes`` is accepted
    only when transitioning to ``response`` (ignored otherwise).
    """

    model_config = ConfigDict(from_attributes=False)

    status: FoiaRequestStatus = Field(description="Target status to transition to.")
    response_notes: str | None = Field(
        default=None,
        description="Free-text notes (accepted on ack→response transition).",
    )


class FoiaRequestRead(BaseModel):
    """A FOIA request as returned by the API.

    Workspace-scoped — the caller's workspace is always the same as
    ``workspace_id``.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    created_by: uuid.UUID
    entity_id: uuid.UUID
    jurisdiction: str | None
    subject: str
    body: str
    submission_method: str
    submission_target: str | None
    status: str
    sent_at: datetime | None
    ack_at: datetime | None
    response_at: datetime | None
    response_notes: str | None
    created_at: datetime
    updated_at: datetime


class FoiaRequestPage(BaseModel):
    """Cursor-paginated list of FOIA requests (doc 06 §5)."""

    model_config = ConfigDict(from_attributes=False)

    items: list[FoiaRequestRead]
    next_cursor: str | None = Field(
        default=None,
        description="Opaque cursor for the next page; null when this is the last page.",
    )


class FoiaRequestEventRead(BaseModel):
    """One status-transition event in a FOIA request's history."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    request_id: uuid.UUID
    actor_id: uuid.UUID
    from_status: str
    to_status: str
    occurred_at: datetime


# ---------------------------------------------------------------------------
# M5 — Reminder config shapes
# ---------------------------------------------------------------------------


class FoiaReminderConfigRead(BaseModel):
    """Current reminder configuration for a FOIA request (M5).

    Returned by ``GET /api/v1/foia/requests/{id}/reminder`` and by the PATCH
    endpoint after a successful update.
    """

    model_config = ConfigDict(from_attributes=False)

    reminder_enabled: bool = Field(
        description="Whether periodic reminder nudges are active for this request."
    )
    reminder_days: int = Field(
        description=(
            "Days after sent_at before the first reminder email fires. "
            "Seeded from the template's statutory deadline at creation time."
        )
    )
    reminder_interval_days: int = Field(
        description="Days between subsequent reminder emails after the first."
    )
    reminder_max: int = Field(
        description="Maximum reminder emails to send. 0 means unlimited."
    )
    last_reminded_at: datetime | None = Field(
        default=None,
        description="UTC timestamp of the most recent reminder email; null if never sent.",
    )
    reminder_count: int = Field(description="Total reminder emails sent so far.")


class FoiaReminderConfigUpdate(BaseModel):
    """PATCH body for ``PATCH /api/v1/foia/requests/{id}/reminder`` (M5).

    All fields are optional; only supplied non-null values are applied.

    ``reminder_days`` and ``reminder_interval_days`` must be >= 1.
    ``reminder_max`` must be >= 0 (0 = unlimited).
    """

    model_config = ConfigDict(from_attributes=False)

    reminder_enabled: bool | None = Field(
        default=None,
        description="Enable or disable reminders for this request.",
    )
    reminder_days: int | None = Field(
        default=None,
        ge=1,
        description="Days after sent_at before the first reminder fires.",
    )
    reminder_interval_days: int | None = Field(
        default=None,
        ge=1,
        description="Days between subsequent reminders.",
    )
    reminder_max: int | None = Field(
        default=None,
        ge=0,
        description="Maximum reminders to send (0 = unlimited).",
    )
