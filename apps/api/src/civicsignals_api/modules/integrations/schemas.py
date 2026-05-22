"""Pydantic request/response shapes for the integrations module (doc 08 §3.6 + L3)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from .models import (
    WEBHOOK_EVENT_TYPES,
    Connection,
    ConnectionStatus,
    FieldMapping,
    IntegrationProviderKind,
    PushErrorCode,
    PushLog,
    PushStatus,
    WebhookDelivery,
    WebhookDeliveryStatus,
    WebhookSubscription,
)

CONNECTION_NAME_MAX_LEN = 160
TARGET_OBJECT_MAX_LEN = 255


class ConnectionCreate(BaseModel):
    """Body for POST /integrations/connections (doc 08 §3.6).

    OAuth-only providers (salesforce/hubspot/slack) start the OAuth flow and the
    response carries a ``redirect_url``. ``default_targets`` is an optional list
    of object types this connection pushes to (e.g. ``["opportunity"]``).
    """

    provider: IntegrationProviderKind
    name: str = Field(min_length=1, max_length=CONNECTION_NAME_MAX_LEN)
    default_targets: list[str] = Field(default_factory=list)


class ConnectionOut(BaseModel):
    """Public connection metadata — never includes token material (doc 08 §3.6)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    provider: IntegrationProviderKind
    name: str
    status: ConnectionStatus
    scopes: list[str]
    default_targets: list[str]
    provider_account: dict[str, object]
    connected_at: datetime | None = None
    last_push_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_orm_connection(cls, connection: Connection) -> ConnectionOut:
        return cls.model_validate(connection)


class ConnectionCreated(BaseModel):
    """POST response: an OAuth provider returns a ``redirect_url`` (doc 08 §3.6)."""

    id: UUID
    provider: IntegrationProviderKind
    status: ConnectionStatus
    redirect_url: str | None = None


class ConnectionList(BaseModel):
    """GET /integrations/connections response (doc 08 §3.6)."""

    data: list[ConnectionOut]


class PushErrorOut(BaseModel):
    """The typed error block in a failed push-log row (doc 08 §3.6)."""

    code: PushErrorCode
    message: str | None = None
    provider_response_id: str | None = None


class PushLogOut(BaseModel):
    """One push-log row (doc 08 §3.6). Secrets are never included."""

    id: UUID
    connection_id: UUID
    signal_id: str | None = None
    pipeline_item_id: str | None = None
    target: str
    status: PushStatus
    external_id: str | None = None
    error: PushErrorOut | None = None
    attempt_count: int
    retry_at: datetime | None = None
    attempted_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_orm_log(cls, log: PushLog) -> PushLogOut:
        error: PushErrorOut | None = None
        if log.error_code is not None:
            error = PushErrorOut(
                code=log.error_code,
                message=log.error_message,
                provider_response_id=log.provider_response_id,
            )
        return cls(
            id=log.id,
            connection_id=log.connection_id,
            signal_id=log.signal_id,
            pipeline_item_id=log.pipeline_item_id,
            target=log.target,
            status=log.status,
            external_id=log.external_id,
            error=error,
            attempt_count=log.attempt_count,
            retry_at=log.retry_at,
            attempted_at=log.attempted_at,
            created_at=log.created_at,
        )


class PushLogPageOut(BaseModel):
    """Cursor-paginated push-log page (doc 08 §3.6)."""

    data: list[PushLogOut]
    next_cursor: str | None = None


# --- Object/field discovery (K2 field-mapping UI) ---------------------------


class DiscoveredObject(BaseModel):
    """One pushable provider object (K2 discovery → object dropdown)."""

    name: str
    label: str
    custom: bool = False


class ObjectDiscoveryOut(BaseModel):
    """GET .../discover/objects response — the connection's pushable objects (K2)."""

    data: list[DiscoveredObject]


class DiscoveredField(BaseModel):
    """One writable provider field (K2 discovery → field-mapping rows)."""

    name: str
    label: str
    type: str
    required: bool = False
    createable: bool = True
    updateable: bool = True


class FieldDiscoveryOut(BaseModel):
    """GET .../discover/fields response — an object's writable fields (K2)."""

    object: str
    data: list[DiscoveredField]


# --- Field mapping CRUD (K2) ------------------------------------------------


class FieldMappingUpsert(BaseModel):
    """Body for saving a connection's field mapping for one target object (K2).

    ``field_map`` maps provider-field-name → source-field-path (e.g.
    ``{"Name": "signal.title"}``); ``constants`` are static literals written on
    every push (e.g. ``{"StageName": "Prospecting"}``).
    """

    target_object: str = Field(min_length=1, max_length=TARGET_OBJECT_MAX_LEN)
    field_map: dict[str, str] = Field(default_factory=dict)
    constants: dict[str, object] = Field(default_factory=dict)


class FieldMappingOut(BaseModel):
    """A saved field mapping (K2)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connection_id: UUID
    target_object: str
    field_map: dict[str, object]
    constants: dict[str, object]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_mapping(cls, mapping: FieldMapping) -> FieldMappingOut:
        return cls.model_validate(mapping)


class FieldMappingList(BaseModel):
    """GET .../field-mappings response — a connection's mappings (K2)."""

    data: list[FieldMappingOut]


# --- Push a signal / pipeline-item (K2) -------------------------------------


class PushRequestIn(BaseModel):
    """Body for POST .../connections/{id}/push (doc 08 §3.2 `POST /signals/{id}/push`).

    ``source`` is the signal/pipeline-item field values to map (integrations
    never reads other modules' tables, so the caller serializes them).
    ``target`` is the object to push to (defaults to the connection's first
    default target / Opportunity); ``field_map_override`` is an inline mapping
    that wins over the saved one (doc 08 §3.2 ``field_map_override``).
    """

    source: dict[str, object] = Field(default_factory=dict)
    target: str | None = None
    field_map_override: dict[str, str] | None = None
    signal_id: str | None = None
    pipeline_item_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=255)


class PushOut(BaseModel):
    """POST .../push response — the resulting push-log row (K2)."""

    push_log: PushLogOut


# ---------------------------------------------------------------------------
# L3: Webhook subscriber CRUD + delivery log schemas
# ---------------------------------------------------------------------------

WEBHOOK_URL_MAX_LEN = 2048
WEBHOOK_DESCRIPTION_MAX_LEN = 255


def _validate_events(events: list[str]) -> list[str]:
    """Validate that every event name is a known type (raises ValueError on unknown)."""
    unknown = [e for e in events if e not in WEBHOOK_EVENT_TYPES]
    if unknown:
        raise ValueError(f"unknown event type(s): {', '.join(sorted(unknown))}")
    if not events:
        raise ValueError("at least one event type is required")
    return events


class WebhookSubscriptionCreate(BaseModel):
    """Body for POST /webhooks (doc 08 §3.6 — shown ONCE, like B8 tokens)."""

    url: AnyHttpUrl
    events: list[str] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=WEBHOOK_DESCRIPTION_MAX_LEN)

    def validated_events(self) -> list[str]:
        """Return the validated event list (raises ValueError on unknown types)."""
        return _validate_events(self.events)


class WebhookSubscriptionCreated(BaseModel):
    """POST /webhooks response — secret revealed ONCE (doc 08 §3.6)."""

    id: UUID
    url: str
    secret: str  # plaintext, shown once; not returned on subsequent reads
    events: list[str]
    active: bool
    description: str | None = None
    created_at: datetime


class WebhookSubscriptionUpdate(BaseModel):
    """Body for PATCH /webhooks/{id} — all fields optional."""

    url: AnyHttpUrl | None = None
    events: list[str] | None = Field(default=None, min_length=1)
    active: bool | None = None
    description: str | None = Field(default=None, max_length=WEBHOOK_DESCRIPTION_MAX_LEN)

    def validated_events(self) -> list[str] | None:
        """Return the validated event list if present."""
        if self.events is None:
            return None
        return _validate_events(self.events)


class WebhookSubscriptionOut(BaseModel):
    """Public subscription metadata — secret NEVER included (L3)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    events: list[str]
    active: bool
    description: str | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm(cls, sub: WebhookSubscription) -> WebhookSubscriptionOut:
        return cls(
            id=sub.id,
            url=sub.url,
            events=sub.subscribed_events,
            active=sub.active,
            description=sub.description,
            created_at=sub.created_at,
            updated_at=sub.updated_at,
        )


class WebhookSubscriptionList(BaseModel):
    """GET /webhooks response."""

    data: list[WebhookSubscriptionOut]


class WebhookDeliveryOut(BaseModel):
    """One delivery log row (L3). Secrets are never included."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subscription_id: UUID
    event_id: UUID
    event_type: str
    status: WebhookDeliveryStatus
    response_status: int | None = None
    attempt_count: int
    retry_at: datetime | None = None
    attempted_at: datetime | None = None
    created_at: datetime

    @classmethod
    def from_orm(cls, d: WebhookDelivery) -> WebhookDeliveryOut:
        return cls(
            id=d.id,
            subscription_id=d.subscription_id,
            event_id=d.event_id,
            event_type=d.event_type,
            status=d.status,
            response_status=d.response_status,
            attempt_count=d.attempt_count,
            retry_at=d.retry_at,
            attempted_at=d.attempted_at,
            created_at=d.created_at,
        )


class WebhookDeliveryPageOut(BaseModel):
    """Cursor-paginated delivery log page (L3)."""

    data: list[WebhookDeliveryOut]
    next_cursor: str | None = None
