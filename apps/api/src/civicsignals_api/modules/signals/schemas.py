"""Strict per-signal-type output schemas for the signals module (E4).

This is the **hard gate** at the end of the extraction funnel (doc 19 §5.1, §6.1):
the permissive ``CandidateRecord`` the extract stage emits is parsed into the
strict, per-signal-type Pydantic model defined here. A candidate that does not
satisfy its type's *required* fields is rejected — the funnel's "missing a
required field → candidate rejected" rule (doc 19 §6.1, hard gate, no exceptions).

The taxonomy is the MVP set (doc 19 §5.1). Each type's required/optional fields
follow doc 19 §6.1 and the ``details_jsonb`` example in doc 07 §5; the fields that
downstream scoring needs (doc 14 §6.2 — title/summary keywords, ``amount_cents``
deal-band, occurred/due dates) are surfaced as typed columns where the type has
them so F3 can read them without re-parsing the JSONB.

Design:

- :class:`SignalType` — the closed enum of MVP signal types.
- :class:`SignalPayload` — the common base (``title`` + ``summary`` are required
  for *every* type so the feed and keyword scoring always have text to work with).
- one strict subclass per type with ``extra="forbid"`` so an unexpected key from a
  drifting prompt is a validation error, not silently dropped.
- :func:`parse_signal_payload` — dispatches on ``signal_type`` and returns the
  validated typed model, raising :class:`SignalValidationError` on any failure
  (unknown type, missing required field, wrong shape). The extract stage turns
  that into the retry → dead-letter path (doc 19 §6.1; E1's task).

The validated payload's ``model_dump`` becomes the signal's ``details_jsonb``
(doc 07 ``signals_signal.details_jsonb``); the promoted-from columns
(``occurred_at``, ``amount_cents``, …) are lifted onto the row by the service.

These schemas are pure data + validation — no DB, no I/O — so they are trivially
unit-testable and importable from the ``extraction`` module (cross-module use goes
through ``signals.services``, which re-exports them).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class SignalType(StrEnum):
    """The MVP signal-type taxonomy (doc 19 §5.1, PRD F6.1)."""

    RFP_POSTED = "rfp_posted"
    RFI_RFQ = "rfi_rfq"
    CONTRACT_EXPIRING = "contract_expiring"
    CONTRACT_AWARDED = "contract_awarded"
    BUDGET_APPROVED = "budget_approved"
    GRANT_AWARDED = "grant_awarded"
    GRANT_OPPORTUNITY = "grant_opportunity"
    LEADERSHIP_CHANGE = "leadership_change"
    BOARD_AGENDA_ITEM = "board_agenda_item"
    STRATEGIC_PLAN_PUBLISHED = "strategic_plan_published"
    OPEN_JOB = "open_job"
    NEWS_MENTION = "news_mention"


# A non-negative monetary amount in cents (doc 14 deal-band is in cents). Kept as a
# reusable annotation so every type's amount field shares the same constraint.
AmountCents = Annotated[int, Field(ge=0)]


class SignalValidationError(Exception):
    """A candidate failed the strict per-type schema gate (doc 19 §6.1).

    Carries the offending ``signal_type`` and a list of human-readable error
    strings (the surfaced validation detail) so the extract stage can dead-letter
    the job with a message an operator can act on, and so the rejection is logged
    for retrospective analysis (doc 19 §6.1: rejected candidates are logged).
    """

    def __init__(self, signal_type: str | None, errors: list[str]) -> None:
        self.signal_type = signal_type
        self.errors = errors
        joined = "; ".join(errors) if errors else "unknown validation error"
        super().__init__(f"signal_type={signal_type!r}: {joined}")


class SignalPayload(BaseModel):
    """Common base for every typed signal payload (doc 19 §6.1, doc 14 §6.2).

    ``title`` and ``summary`` are required for *all* signal types: the feed shows
    them and keyword scoring (doc 14 §6.2) runs over them, so a signal without
    either is not useful and is rejected. ``extra="forbid"`` makes an unexpected
    field from a drifting prompt a hard validation error (doc 19 §13.2).
    """

    model_config = ConfigDict(extra="forbid")

    signal_type: SignalType
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)


class RFPPostedPayload(SignalPayload):
    """``rfp_posted`` — a formal solicitation being issued (doc 19 §5.1, §6.1).

    Required: title, summary, ``due_at`` (the submission deadline that makes it an
    RFP rather than a vague mention — doc 19 §5.3). Optional: rfp number, estimated
    value, posting agency/contact, submission url, requirements (doc 07 §5 example).
    """

    signal_type: Literal[SignalType.RFP_POSTED] = SignalType.RFP_POSTED
    due_at: datetime
    rfp_number: str | None = None
    amount_cents: AmountCents | None = None
    posting_agency: str | None = None
    contact_name: str | None = None
    submission_url: str | None = None
    requirements: list[str] = Field(default_factory=list)


class RFIRFQPayload(SignalPayload):
    """``rfi_rfq`` — a pre-RFP information-gathering solicitation (doc 19 §5.1)."""

    signal_type: Literal[SignalType.RFI_RFQ] = SignalType.RFI_RFQ
    due_at: datetime | None = None
    reference_number: str | None = None
    posting_agency: str | None = None
    submission_url: str | None = None


class ContractExpiringPayload(SignalPayload):
    """``contract_expiring`` — a contract ending soon (doc 19 §5.1, §6.1).

    Required: vendor name + ``expires_at`` (doc 19 §6.1). The vendor + expiry are
    the dedup-key fields (doc 19 §7.1).
    """

    signal_type: Literal[SignalType.CONTRACT_EXPIRING] = SignalType.CONTRACT_EXPIRING
    vendor_name: str = Field(min_length=1)
    expires_at: datetime
    amount_cents: AmountCents | None = None
    contract_number: str | None = None


class ContractAwardedPayload(SignalPayload):
    """``contract_awarded`` — a contract awarded to a vendor (doc 19 §5.1)."""

    signal_type: Literal[SignalType.CONTRACT_AWARDED] = SignalType.CONTRACT_AWARDED
    vendor_name: str = Field(min_length=1)
    awarded_at: datetime | None = None
    amount_cents: AmountCents | None = None
    contract_number: str | None = None


class BudgetApprovedPayload(SignalPayload):
    """``budget_approved`` — a budget allocation/approval (doc 19 §5.1, §6.1).

    Required: amount, category, fiscal year (doc 19 §6.1). Optional: line items,
    approving body.
    """

    signal_type: Literal[SignalType.BUDGET_APPROVED] = SignalType.BUDGET_APPROVED
    amount_cents: AmountCents
    category: str = Field(min_length=1)
    fiscal_year: str = Field(min_length=1)
    line_items: list[str] = Field(default_factory=list)
    approving_body: str | None = None


class GrantAwardedPayload(SignalPayload):
    """``grant_awarded`` — a grant received (doc 19 §5.1)."""

    signal_type: Literal[SignalType.GRANT_AWARDED] = SignalType.GRANT_AWARDED
    amount_cents: AmountCents | None = None
    funder: str | None = None
    program: str | None = None
    awarded_at: datetime | None = None


class GrantOpportunityPayload(SignalPayload):
    """``grant_opportunity`` — a new grant program open for applications (§5.1)."""

    signal_type: Literal[SignalType.GRANT_OPPORTUNITY] = SignalType.GRANT_OPPORTUNITY
    funder: str | None = None
    program: str | None = None
    amount_cents: AmountCents | None = None
    due_at: datetime | None = None
    application_url: str | None = None


class LeadershipChangePayload(SignalPayload):
    """``leadership_change`` — a personnel change (doc 19 §5.1, §6.1).

    Required: role + person name (doc 19 §6.1). Role + person are the dedup-key
    fields (doc 19 §7.1).
    """

    signal_type: Literal[SignalType.LEADERSHIP_CHANGE] = SignalType.LEADERSHIP_CHANGE
    role: str = Field(min_length=1)
    person_name: str = Field(min_length=1)
    previous_holder: str | None = None
    effective_date: date | None = None


class BoardAgendaItemPayload(SignalPayload):
    """``board_agenda_item`` — an upcoming agenda item (doc 19 §5.1, §7.1).

    Required: meeting date + topic (the dedup-key fields, doc 19 §7.1).
    """

    signal_type: Literal[SignalType.BOARD_AGENDA_ITEM] = SignalType.BOARD_AGENDA_ITEM
    meeting_date: datetime
    topic: str = Field(min_length=1)
    agenda_url: str | None = None


class StrategicPlanPublishedPayload(SignalPayload):
    """``strategic_plan_published`` — a new multi-year plan (doc 19 §5.1)."""

    signal_type: Literal[SignalType.STRATEGIC_PLAN_PUBLISHED] = SignalType.STRATEGIC_PLAN_PUBLISHED
    plan_type: str | None = None
    published_at: date | None = None
    plan_url: str | None = None
    horizon_years: int | None = Field(default=None, ge=1)


class OpenJobPayload(SignalPayload):
    """``open_job`` — a posted job in a procurement-relevant role (doc 19 §5.1)."""

    signal_type: Literal[SignalType.OPEN_JOB] = SignalType.OPEN_JOB
    role: str = Field(min_length=1)
    department: str | None = None
    posting_url: str | None = None
    closes_at: datetime | None = None


class NewsMentionPayload(SignalPayload):
    """``news_mention`` — a news article in a procurement context (doc 19 §5.1)."""

    signal_type: Literal[SignalType.NEWS_MENTION] = SignalType.NEWS_MENTION
    article_url: str | None = None
    publication: str | None = None
    published_at: date | None = None


# Dispatch table: signal type -> its strict payload model. Adding a new signal type
# (doc 19 §13.2 — the taxonomy grows) is a new entry here plus the enum member.
PAYLOAD_BY_TYPE: dict[SignalType, type[SignalPayload]] = {
    SignalType.RFP_POSTED: RFPPostedPayload,
    SignalType.RFI_RFQ: RFIRFQPayload,
    SignalType.CONTRACT_EXPIRING: ContractExpiringPayload,
    SignalType.CONTRACT_AWARDED: ContractAwardedPayload,
    SignalType.BUDGET_APPROVED: BudgetApprovedPayload,
    SignalType.GRANT_AWARDED: GrantAwardedPayload,
    SignalType.GRANT_OPPORTUNITY: GrantOpportunityPayload,
    SignalType.LEADERSHIP_CHANGE: LeadershipChangePayload,
    SignalType.BOARD_AGENDA_ITEM: BoardAgendaItemPayload,
    SignalType.STRATEGIC_PLAN_PUBLISHED: StrategicPlanPublishedPayload,
    SignalType.OPEN_JOB: OpenJobPayload,
    SignalType.NEWS_MENTION: NewsMentionPayload,
}


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """Flatten a Pydantic ValidationError into short, operator-readable strings."""
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        out.append(f"{loc}: {err.get('msg', 'invalid')}")
    return out


def parse_signal_payload(signal_type: str | None, fields: dict[str, object]) -> SignalPayload:
    """Validate a candidate's fields against its strict per-type schema (doc 19 §6.1).

    This is the hard gate. ``signal_type`` is the coarse type the extract stage
    guessed; ``fields`` is the permissive ``CandidateRecord.fields`` payload. The
    ``signal_type`` is injected into the payload (the typed models pin it via a
    ``Literal`` so a mismatched type in ``fields`` is itself a validation error).

    Raises :class:`SignalValidationError` — never a bare ``pydantic.ValidationError``
    — so the extraction module can catch one well-defined exception and route it to
    the retry → dead-letter path (doc 19 §6.1). An unknown/absent ``signal_type`` is
    rejected the same way (we cannot validate against a schema we do not have).
    """
    if signal_type is None:
        raise SignalValidationError(None, ["signal_type: candidate has no signal_type"])
    try:
        type_enum = SignalType(signal_type)
    except ValueError as exc:
        raise SignalValidationError(
            signal_type, [f"signal_type: unknown signal type {signal_type!r}"]
        ) from exc

    model_cls = PAYLOAD_BY_TYPE[type_enum]
    # Build the payload from the candidate fields plus the injected type. A
    # ``signal_type`` already present in ``fields`` is allowed only if it matches
    # (the Literal enforces that); we set it explicitly so callers need not.
    payload = {**fields, "signal_type": type_enum}
    try:
        return model_cls.model_validate(payload)
    except ValidationError as exc:
        raise SignalValidationError(signal_type, _format_validation_errors(exc)) from exc


# --- Read shapes (the G1 feed / signals API read seam; doc 08) -----------------


class SignalRead(BaseModel):
    """A persisted ``signals_signal`` row, the read shape returned by the API.

    The canonical global signal (doc 07 §2 "signals"). ``details`` is the validated
    per-type payload (the ``details_jsonb`` column); the lifted columns
    (``occurred_at``, ``amount_cents`` via the payload, …) are also present on the
    row. Workspace-specific scoring lives in ``signals_workspace_score`` (F3), never
    here — the signal is global (doc 07 §3, doc 14 §4.2).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    # Nullable: entity resolution may be pending (doc 19 §4.3) — the signal carries
    # ``entity_name_raw`` until E10 resolves it.
    entity_id: uuid.UUID | None
    entity_name_raw: str | None
    signal_type: str
    recipe_id: str
    raw_document_ids: list[uuid.UUID]
    content_hash: str
    occurred_at: datetime | None
    observed_at: datetime
    summary: str
    title: str
    details: dict[str, object]
    confidence: float | None
    status: str
    is_degraded: bool
    review_required: bool
    created_at: datetime


class SignalPage(BaseModel):
    """A cursor-paginated page of signals (doc 06 §5)."""

    model_config = ConfigDict(extra="forbid")

    items: list[SignalRead]
    next_cursor: str | None = None


# --- G2 signal-detail read shapes (the detail page; doc 14 §5.3) ---------------


class SourceDocumentRead(BaseModel):
    """One corroborating source document on the signal-detail page (G2).

    A provenance projection of an ``ingestion_raw_document`` row: where the signal
    came from and when it was fetched. ``missing`` flags a referenced id that no
    longer resolves (a doc pruned after the signal was stored).
    """

    model_config = ConfigDict(from_attributes=True)

    raw_document_id: uuid.UUID
    recipe_id: str | None
    source_url: str | None
    fetched_at: datetime | None
    content_type: str | None
    missing: bool = False


class SuggestedContactRead(BaseModel):
    """One suggested contact at the signal's entity on the detail page (G2).

    A projection of a global ``contacts_contact`` row (contacts are global per
    entity, doc 07 §3) — surfaced so a user acting on a signal can reach the right
    person without leaving the page.
    """

    model_config = ConfigDict(from_attributes=True)

    contact_id: uuid.UUID
    name: str
    title: str | None
    department: str | None
    canonical_email: str | None
    status: str
    verified: bool


class RelatedSignalRead(BaseModel):
    """One related signal about the same entity on the detail page (G2)."""

    model_config = ConfigDict(from_attributes=False)

    signal: SignalRead


class SignalDetailRead(BaseModel):
    """The full signal-detail view for one (workspace, signal) pair (G2).

    The global signal plus the calling workspace's score / status / breakdown (all
    ``None`` when the signal did not score into this workspace's feed — the corpus is
    global, so a signal is viewable by id regardless), the validated extracted
    fields, the corroborating source documents, the suggested contacts at the
    signal's entity, and the related signals about the same entity (doc 14 §5.3).
    """

    model_config = ConfigDict(extra="forbid")

    signal: SignalRead
    entity_id: uuid.UUID | None
    entity_name: str | None
    score: float | None
    status: str | None
    score_breakdown: dict[str, object] | None
    matched_keywords: list[str]
    extracted_fields: dict[str, object]
    source_documents: list[SourceDocumentRead]
    suggested_contacts: list[SuggestedContactRead]
    related_signals: list[RelatedSignalRead]
