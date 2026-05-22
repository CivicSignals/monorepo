"""integrations SQLAlchemy models (K1 + L3).

Tables are prefixed ``integrations_`` and are migrated only by this module
(doc 06 §3, §4). This is the generic outbound-integration framework that K2
(Salesforce), K3 (HubSpot), K4 (idempotent push), K5 (recovery), L1 (Slack) and
L3 (webhooks) build on:

- :class:`Connection` — one connected provider account per workspace. Holds the
  OAuth tokens **encrypted at rest** (Fernet, not hashed — OAuth tokens must be
  recoverable to call the provider, unlike API tokens which are hashed; B8 /
  threat-model §4.2). The plaintext ``access_token`` / ``refresh_token`` are
  never persisted; only their ciphertext is. ``services.token_cipher`` is the
  only place they are decrypted, and they are never logged.
- :class:`PushLog` — an append-row-per-attempt record of every outbound push
  (signal/pipeline-item → provider object). Carries the typed error code for
  scope-aware error mapping (K5 recovery UI) and the retry/dead-letter state the
  ``retry_failed_pushes`` task drives.
- :class:`FieldMapping` — a per-connection signal→provider-object field mapping
  (K2). Drives how a push shapes the vendor body; the field-mapping UI edits it.
- :class:`WebhookSubscription` (L3) — a workspace's outbound webhook endpoint.
  The HMAC secret is stored **encrypted** (Fernet, same as OAuth tokens — it must
  be recoverable to sign deliveries). The plaintext secret is shown ONCE at create
  time (doc 08 §3.6) and never returned again (like B8 API tokens, the column is
  never included in responses). ``services.token_cipher`` handles encrypt/decrypt.
- :class:`WebhookDelivery` (L3) — an append-row-per-attempt audit of every
  outbound webhook delivery. Carries the raw request/response (secrets scrubbed),
  success/failure, attempt count, and retry/dead-letter scheduling.

All tables are workspace-scoped (B5) via ``accounts_workspace.id``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import ARRAY, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class IntegrationProviderKind(StrEnum):
    """The provider a connection targets (doc 08 §3.6, doc 03 F11/F12/L3).

    - ``salesforce`` / ``hubspot`` — OAuth2 authorization-code CRMs (K2/K3).
    - ``slack`` — OAuth2 workspace install (L1).
    - ``webhook`` — a generic outbound HTTP webhook with a shared secret (L3);
      not OAuth — the secret is supplied at create and stored encrypted.
    """

    SALESFORCE = "salesforce"
    HUBSPOT = "hubspot"
    SLACK = "slack"
    WEBHOOK = "webhook"


class ConnectionStatus(StrEnum):
    """Lifecycle state of a connection (doc 08 §3.6).

    - ``pending_oauth`` — created; the OAuth consent redirect has not completed.
    - ``healthy`` — tokens present and the last push/refresh succeeded.
    - ``degraded`` — recent pushes are failing for a recoverable reason
      (rate-limit, transient) but the connection is still authorized.
    - ``needs_reauth`` — the token is invalid/expired and refresh failed; the
      admin must re-run OAuth (K5 recovery surfaces this; doc 04 J7 "reconnect").
    - ``revoked`` — disconnected by an admin; retained for the push-log audit.
    """

    PENDING_OAUTH = "pending_oauth"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    NEEDS_REAUTH = "needs_reauth"
    REVOKED = "revoked"


class PushStatus(StrEnum):
    """Outcome of a single push attempt (doc 08 §3.6 push-log).

    - ``pending`` — queued, not yet attempted.
    - ``success`` — the provider accepted the push (``external_id`` set).
    - ``failed`` — a recoverable failure; ``retry_at`` is set and the
      ``retry_failed_pushes`` task will retry until ``max_attempts``.
    - ``dead_letter`` — exhausted retries (or a non-retryable error); requires
      operator/recovery-UI attention (K5).
    """

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class PushErrorCode(StrEnum):
    """Scope-aware, provider-agnostic error taxonomy (K1; K5 recovery UI).

    Concrete provider clients (K2/K3/L1) map their vendor errors onto these so
    the recovery UI can react uniformly: ``auth``/``permission`` → prompt
    re-auth; ``rate_limited`` → back off and retry; ``validation`` → surface the
    field error to the user (non-retryable); ``transient``/``unknown`` → retry.
    """

    AUTH = "auth"  # 401: invalid/expired credentials → needs_reauth
    PERMISSION = "permission"  # 403: token lacks the required scope/permission
    RATE_LIMITED = "rate_limited"  # 429: back off and retry
    VALIDATION = "validation"  # 4xx field/payload error → non-retryable
    NOT_FOUND = "not_found"  # 404: target object missing
    TRANSIENT = "transient"  # 5xx / network → retry
    UNKNOWN = "unknown"  # unclassified


# Error codes that should NOT be retried (the push goes straight to dead-letter
# or stays failed for manual intervention). Auth/permission/validation/not_found
# will not resolve by retrying the same payload with the same token.
NON_RETRYABLE_ERROR_CODES: frozenset[PushErrorCode] = frozenset(
    {
        PushErrorCode.PERMISSION,
        PushErrorCode.VALIDATION,
        PushErrorCode.NOT_FOUND,
    }
)


class Connection(Base):
    """A connected outbound-integration account, scoped to one workspace (K1).

    OAuth tokens are stored **encrypted** (``access_token_encrypted`` /
    ``refresh_token_encrypted``), never in cleartext and never hashed (they must
    be decryptable to call the provider). The ``services.token_cipher`` helper
    is the only decryption point; tokens are never logged (threat-model §4.2).

    ``provider_account`` carries non-secret external metadata returned by the
    provider at connect time (e.g. Salesforce ``instance_url`` + org id, Slack
    team id/name) so the UI can label the connection and clients can route calls.
    """

    __tablename__ = "integrations_connection"
    __table_args__ = (
        Index("ix_integrations_connection_workspace", "workspace_id"),
        Index("ix_integrations_connection_workspace_provider", "workspace_id", "provider"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )

    provider: Mapped[IntegrationProviderKind] = mapped_column(
        Enum(
            IntegrationProviderKind,
            name="integrations_provider",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(
            ConnectionStatus,
            name="integrations_connection_status",
            native_enum=False,
            length=32,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=ConnectionStatus.PENDING_OAUTH,
    )

    # Human label, e.g. "Acme SLED production" (doc 08 §3.6).
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Encrypted OAuth/secret material (Fernet ciphertext; NULL until OAuth
    # completes or an API-key secret is supplied). Never logged.
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Scopes granted by the provider (subset/superset of what we requested).
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, server_default="{}")

    # Non-secret external account metadata (instance_url, team id/name, org id…).
    provider_account: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Default target object types this connection pushes to (e.g. ["opportunity"]).
    default_targets: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )

    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="SET NULL"),
        nullable=True,
    )

    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def is_token_expired(self) -> bool:
        """True iff there is a known expiry and it is in the past.

        Used by the auto-refresh seam: an expired (or about-to-expire) token is
        refreshed before a push. A connection with no ``token_expires_at`` is
        treated as non-expiring here (e.g. webhook secrets, long-lived tokens).
        """
        if self.token_expires_at is None:
            return False
        expires_at = self.token_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at <= datetime.now(UTC)


class FieldMapping(Base):
    """A per-connection signal→provider-object field mapping (K2).

    Maps source fields (signal / pipeline-item paths, e.g.
    ``signal.title`` → Salesforce ``Name``) onto a provider object's fields so a
    push can shape the vendor body declaratively. One row per (connection,
    target object); the field-mapping UI (K2) edits ``field_map`` and the push
    service applies it. ``constants`` carries static values written on every push
    (e.g. a fixed ``StageName``). Workspace-scoped (B5) for isolation.

    # TODO K6: save a mapping as a reusable template (per-connection default ↔ a
    #   workspace/library template); this row is the per-connection instance K6
    #   seeds from / saves to.
    """

    __tablename__ = "integrations_field_mapping"
    __table_args__ = (
        Index("ix_integrations_field_mapping_workspace", "workspace_id"),
        Index("ix_integrations_field_mapping_connection", "connection_id"),
        # One mapping per (connection, target object) — the UI upserts on save.
        Index(
            "uq_integrations_field_mapping_connection_target",
            "connection_id",
            "target_object",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations_connection.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The provider object this mapping targets (e.g. "Opportunity", "Deal__c").
    target_object: Mapped[str] = mapped_column(String(255), nullable=False)

    # provider-field-name → source-field-path (e.g. {"Name": "signal.title"}).
    # The source path is resolved against the push source by the push service.
    field_map: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # provider-field-name → static literal written on every push (e.g. StageName).
    constants: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PushLog(Base):
    """One outbound-push attempt record (K1; doc 08 §3.6 push-log).

    Append-oriented audit of every push: which connection, what target object,
    the (redacted) request and provider response, success/failure, the typed
    error for scope-aware mapping, and the retry/dead-letter bookkeeping the
    ``retry_failed_pushes`` task drives.

    K4 (idempotent push) uses ``external_id`` + ``idempotency_key`` to dedupe a
    re-push of the same signal into an upsert rather than a duplicate create.
    """

    __tablename__ = "integrations_push_log"
    __table_args__ = (
        Index("ix_integrations_push_log_workspace", "workspace_id"),
        Index("ix_integrations_push_log_connection", "connection_id"),
        Index("ix_integrations_push_log_status", "status"),
        # Retry sweeper scans failed rows whose retry_at has passed.
        Index("ix_integrations_push_log_retry", "status", "retry_at"),
        # K4: dedupe a re-push of the same source object per connection.
        Index("ix_integrations_push_log_idempotency", "connection_id", "idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations_connection.id", ondelete="CASCADE"),
        nullable=False,
    )

    # What was pushed. ``signal_id`` / ``pipeline_item_id`` are intentionally
    # un-FK'd strings: integrations must not import other modules' tables (doc 06
    # §3) and the push-log is evidence that should survive source deletion.
    signal_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    pipeline_item_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Provider-qualified target, e.g. "salesforce.opportunity" (doc 08 §3.6).
    target: Mapped[str] = mapped_column(String(120), nullable=False)

    status: Mapped[PushStatus] = mapped_column(
        Enum(
            PushStatus,
            name="integrations_push_status",
            native_enum=False,
            length=24,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=PushStatus.PENDING,
    )

    # The request payload sent to the provider (already redacted of secrets by
    # the caller — this is stored and may be surfaced in the recovery UI).
    request: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # The provider's response body / status (best-effort, redacted).
    response: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)

    # TODO K4: the provider-side id of the created/updated object; presence makes
    # a re-push an idempotent update. ``idempotency_key`` is the client-supplied
    # (or derived) key that ties repeated push attempts of one source object
    # together so K4 can upsert instead of duplicating.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    # Scope-aware typed error (K5 recovery UI branches on this) + human message.
    error_code: Mapped[PushErrorCode | None] = mapped_column(
        Enum(
            PushErrorCode,
            name="integrations_push_error_code",
            native_enum=False,
            length=24,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_response_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PushIdempotency(Base):
    """Canonical external-id registry for idempotent pushes (K4).

    One row per ``(connection_id, idempotency_key)`` — the stable mapping from
    a logical push identity to the provider's external id.  This table is
    **upserted** on each successful push so that:

    - Sequential re-pushes look up the existing ``external_id`` before calling
      the provider and route through the provider's update path (no duplicate
      CRM objects).
    - Concurrent duplicate pushes race on the unique primary key: the winner's
      INSERT succeeds; the loser's INSERT hits a conflict, raises
      ``IntegrityError``, and is retried as an UPDATE.

    ``push_log`` remains a pure append-only audit trail (no uniqueness
    constraint on it) while this table is the single source of truth for
    which external id belongs to each (connection, idempotency_key) pair.
    """

    __tablename__ = "integrations_push_idempotency"
    # No extra indexes needed: (connection_id, idempotency_key) is the PK.

    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations_connection.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        primary_key=True,
        nullable=False,
    )

    # The provider-side id of the created CRM object.  Presence proves the
    # push succeeded; the push service passes it back as ``external_id`` on
    # re-push so the provider routes the call as an update.
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# L3: Outbound webhook subscriber + delivery log
# ---------------------------------------------------------------------------

# Well-known event types subscribers may filter on (mirrors events.py constants
# + the doc 08 §1.10 webhook event list). Stored as plain strings in the
# subscribed_events ARRAY so new types are addable without a schema migration.
WEBHOOK_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "signal.created",
        "signal.scored",
        "signal.pushed",
        "foia.transitioned",
        "pipeline.item.created",
        "pipeline.item.updated",
        "member.invited",
        "member.joined",
    }
)

# Retry schedule for failed deliveries (doc 08 §1.10 "exponential backoff").
# Attempt 0 is immediate; attempt N waits _WEBHOOK_RETRY_DELAYS[N-1] seconds.
# After the last delay the delivery is dead-lettered.
WEBHOOK_RETRY_DELAYS: tuple[int, ...] = (
    60,  # attempt 2: 1 minute
    300,  # attempt 3: 5 minutes
    1800,  # attempt 4: 30 minutes
    7200,  # attempt 5: 2 hours
    43200,  # attempt 6: 12 hours
)
WEBHOOK_MAX_ATTEMPTS: int = len(WEBHOOK_RETRY_DELAYS) + 1  # 7 total (incl. first)


class WebhookDeliveryStatus(StrEnum):
    """Outcome / lifecycle state of a single webhook delivery attempt (L3).

    - ``pending``   — queued, not yet attempted (first delivery not started).
    - ``success``   — the subscriber returned 2xx.
    - ``failed``    — non-2xx; ``retry_at`` is set and the retry task will retry.
    - ``dead_letter`` — exhausted all retries (or non-retryable); manual attention.
    """

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


class WebhookSubscription(Base):
    """An outbound webhook endpoint registered by a workspace admin (L3).

    The HMAC secret (``secret_encrypted``) is Fernet-encrypted at rest (same
    cipher as OAuth tokens — it must be recoverable to sign deliveries). The
    plaintext secret is returned **once** at create time and never again (like B8
    API tokens). The column is never included in API responses.

    ``subscribed_events`` is a Postgres ARRAY of plain-string event type names
    (e.g. ``["signal.created", "signal.scored"]``). An empty array means "all
    events" is not the semantics we want — subscribers must name at least one.

    ``event_id_counter`` is unused here (event ids are generated per-delivery).
    The subscription row is the durable record; deliveries are in
    :class:`WebhookDelivery`.
    """

    __tablename__ = "integrations_webhook_subscription"
    __table_args__ = (
        Index("ix_integrations_webhook_subscription_workspace", "workspace_id"),
        Index(
            "ix_integrations_webhook_subscription_workspace_active",
            "workspace_id",
            "active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Target URL — the subscriber's endpoint. No FK (external URL).
    url: Mapped[str] = mapped_column(Text, nullable=False)

    # HMAC-SHA256 secret, Fernet-encrypted at rest. Never logged or returned.
    # Revealed ONCE at create time (doc 08 §3.6 "shown ONCE"), then irretrievable
    # through the API — the admin must rotate if lost.
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # Event type filter. Non-empty list; at least one type required at create.
    subscribed_events: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, server_default="{}"
    )

    active: Mapped[bool] = mapped_column(nullable=False, default=True)

    # Human label, e.g. "My Zapier hook" (optional).
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Audit: who created this subscription.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WebhookDelivery(Base):
    """One outbound webhook delivery attempt (L3; doc 08 §1.10).

    Append-oriented audit of every delivery: which subscription, which event,
    the event id for idempotency/re-delivery detection, the request headers +
    body (secret scrubbed), the response status + body (best-effort), success/
    failure, attempt count, and the retry/dead-letter bookkeeping the
    ``retry_failed_webhook_deliveries`` task drives.

    The ``event_id`` is a stable UUID generated once per logical delivery (the
    first attempt). Re-deliveries share the same ``event_id`` so the subscriber
    can detect duplicates via ``X-CivicSignals-Delivery``.

    ``request_body`` is stored (size-bounded by the events bus payload size) so
    the retry task can re-POST the exact same body. ``response_body`` is truncated
    to the first 4 KB by the delivery helper (best-effort diagnostic).
    """

    __tablename__ = "integrations_webhook_delivery"
    __table_args__ = (
        Index("ix_integrations_webhook_delivery_subscription", "subscription_id"),
        Index("ix_integrations_webhook_delivery_workspace", "workspace_id"),
        Index("ix_integrations_webhook_delivery_status", "status"),
        # Retry sweeper: find failed rows whose retry_at has passed.
        Index("ix_integrations_webhook_delivery_retry", "status", "retry_at"),
        # Idempotency: a subscriber can detect a re-delivery by event_id.
        Index("ix_integrations_webhook_delivery_event_id", "event_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The subscription this delivery is for.
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("integrations_webhook_subscription.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Stable delivery id (revealed to subscriber as ``X-CivicSignals-Delivery``).
    # All retry attempts for one logical event share this id so the subscriber
    # can detect re-deliveries.
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid7)

    # The event type that triggered this delivery (e.g. "signal.created").
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)

    # The request body we POST to the subscriber (plain JSON; no secrets).
    request_body: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Request headers we sent (excluding the signature header, which is
    # re-computed on retry from the stored body + secret; storing it here
    # would be redundant and slightly misleading).
    request_headers: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    # Response from the subscriber (best-effort; may be None on network error).
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(
            WebhookDeliveryStatus,
            name="integrations_webhook_delivery_status",
            native_enum=False,
            length=24,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=WebhookDeliveryStatus.PENDING,
    )

    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
