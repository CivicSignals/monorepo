"""foia SQLAlchemy models (M2).

Tables are prefixed ``foia_`` and are migrated only by this module (doc 06 §3,
§4). Two tables:

- ``foia_request`` — workspace-scoped FOIA / public-records request with a
  four-status state machine (draft → sent → ack → response) and optional
  template reference.
- ``foia_request_event`` — immutable status-transition history (one row per
  transition). Preferred over a single ``status_history JSONB`` column so that
  partial-index queries, future automation (M5 reminders), and audit are easy.
  This defers the J3-style audit-event bus (future) but uses the same shape so
  migration to the bus is a pure add.

State machine (doc 04 J8, doc 07 §4, task M2):
    draft → sent → ack → response
                ↘ (terminal: archived — out of scope for MVP; all edits while
                   draft; no edits after sent)

``submission_method`` in the spec's ``foia_request`` table uses values
``email | portal | mail | in_person``; the task spec describes the values as
``draft | sent | ack | response``. The *status* field owns the state machine;
``submission_method`` is how the request was (or will be) physically sent (set
by the user at creation/send time).

Timestamps set by :func:`services.transition_request`:
- ``sent_at``    — set on draft→sent transition.
- ``ack_at``     — set on sent→ack transition.
- ``response_at`` — set on ack→response transition.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class FoiaRequestStatus(StrEnum):
    """FOIA request lifecycle statuses (doc 07 §4, M2 task spec).

    Allowed transitions (enforced by the service layer):
        draft → sent → ack → response

    ``draft`` is the only editable status; all other statuses are immutable
    after transition (the ``update_request`` service raises
    :exc:`FoiaDraftOnlyError` if the request is not ``draft``).

    Note: the spec names the second state "sent" (manual-send MVP). The data
    model doc 07 uses "submitted" for the same concept; we prefer "sent" here
    to match the task brief and to avoid confusion with HTTP submission.
    """

    DRAFT = "draft"
    SENT = "sent"
    ACK = "ack"
    RESPONSE = "response"


# Allowed status transitions. Every key is an allowed *current* status; the
# value is the set of statuses it may advance to. ``draft`` is the only
# editable status and the only one that can move to ``sent``.
ALLOWED_TRANSITIONS: dict[FoiaRequestStatus, set[FoiaRequestStatus]] = {
    FoiaRequestStatus.DRAFT: {FoiaRequestStatus.SENT},
    FoiaRequestStatus.SENT: {FoiaRequestStatus.ACK},
    FoiaRequestStatus.ACK: {FoiaRequestStatus.RESPONSE},
    FoiaRequestStatus.RESPONSE: set(),  # terminal
}


class SubmissionMethod(StrEnum):
    """Physical method used to submit the FOIA request (doc 07 §2 foia_request).

    ``manual`` covers the MVP case where the user copies and sends the rendered
    body themselves. ``email`` / ``portal`` / ``mail`` / ``in_person`` are the
    canonical future options when CivicSignals can automate / pre-fill the send.
    """

    MANUAL = "manual"
    EMAIL = "email"
    PORTAL = "portal"
    MAIL = "mail"
    IN_PERSON = "in_person"


class FoiaRequest(Base):
    """A FOIA / public-records request (workspace-scoped, M2).

    Created by a workspace member targeting a specific entity/agency. Starts in
    ``draft`` status; advances through ``sent → ack → response`` via explicit
    transition calls (:func:`services.transition_request`). Only ``draft``
    requests can be edited.

    ``jurisdiction`` mirrors the template jurisdiction code (e.g. ``"CA-PRA"``)
    used to generate the initial body. It is stored redundantly so the request
    is self-describing even if the in-memory template registry changes.

    ``subject`` is a short human-readable description of the records requested
    (e.g. "Vendor contracts 2022-2024 > $10k"). ``body`` is the full rendered /
    manually-typed request text.

    ``submission_method`` defaults to ``manual`` for MVP (the user copies the
    rendered body and sends it themselves). The ``# TODO M-assisted-send`` note
    below marks the seam where v2 assisted-send (automated email/portal
    submission) will plug in.

    ``notes`` is a free-text field for the requester's internal notes after
    receiving a response (e.g. "partial response — records officer says exempt
    portions will follow within 30 days").
    """

    __tablename__ = "foia_request"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'sent', 'ack', 'response')",
            name="ck_foia_request_status",
        ),
        CheckConstraint(
            "submission_method IN ('manual', 'email', 'portal', 'mail', 'in_person')",
            name="ck_foia_request_submission_method",
        ),
        Index("ix_foia_request_workspace_id", "workspace_id"),
        Index("ix_foia_request_created_by", "created_by"),
        Index("ix_foia_request_entity_id", "entity_id"),
        Index("ix_foia_request_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    # Workspace scoping (B5).
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The member (user) who created this request.
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Target agency/entity (C1 — global entity directory).
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities_entity.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Template reference (nullable — request may be created freeform without a
    # template). Stored as the jurisdiction code string (e.g. "CA-PRA") because
    # FOIA templates are file-backed in M1 (no DB table for foia_template yet).
    jurisdiction: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Human-readable subject / description of the records requested.
    subject: Mapped[str] = mapped_column(String(512), nullable=False)

    # Full request body (rendered template or freeform text).
    body: Mapped[str] = mapped_column(Text, nullable=False)

    # How the request was / will be physically submitted.
    # TODO M-assisted-send: v2 will automate email/portal sends; at MVP the user
    #   manually copies the rendered body. The method is stored so it can be
    #   switched to email/portal when the send-assistant (v2) is implemented
    #   without a migration.
    submission_method: Mapped[str] = mapped_column(
        Enum(
            SubmissionMethod,
            name="foia_submission_method",
            native_enum=False,
            length=16,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=SubmissionMethod.MANUAL.value,
    )

    # Where to send the request (email address, portal URL, mailing address).
    submission_target: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # State machine status.
    status: Mapped[str] = mapped_column(
        Enum(
            FoiaRequestStatus,
            name="foia_request_status",
            native_enum=False,
            length=16,
            values_callable=lambda e: [s.value for s in e],
        ),
        nullable=False,
        server_default=FoiaRequestStatus.DRAFT.value,
    )

    # Transition timestamps (set by services.transition_request on the relevant
    # status transition; NULL until that transition occurs).
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ack_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Free-text notes from the requester after receiving a response.
    response_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class FoiaRequestEvent(Base):
    """Immutable status-transition history for a FOIA request (M2).

    One row is appended per transition by :func:`services.transition_request`.
    The history is not exposed on the list endpoint to keep the response lean;
    callers interested in the full audit trail fetch
    ``GET /api/v1/foia/requests/{id}/events``.

    This table is intentionally simple: it is the right seam for J3-style
    activity-log integration (future) — replace the insert here with a
    publish-to-event-bus call when the activity log module lands.
    """

    __tablename__ = "foia_request_event"
    __table_args__ = (Index("ix_foia_request_event_request_id", "request_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("foia_request.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The user who triggered the transition.
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="RESTRICT"),
        nullable=False,
    )

    from_status: Mapped[str] = mapped_column(String(16), nullable=False)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
