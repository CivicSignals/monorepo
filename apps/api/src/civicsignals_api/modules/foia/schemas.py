"""Pydantic request/response shapes for the foia module (doc 06 §3).

Template responses are **global reference data** — not workspace-scoped —
so these shapes carry no workspace fields. The template list response uses
``items`` + ``total`` (not cursor-paginated) because the library is small
and static (M1). M2 may introduce cursor pagination if the library grows
to the point where a single response is impractical.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


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
