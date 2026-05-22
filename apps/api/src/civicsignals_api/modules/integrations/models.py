"""integrations SQLAlchemy models (K1).

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

Both tables are workspace-scoped (B5) via ``accounts_workspace.id``.
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
