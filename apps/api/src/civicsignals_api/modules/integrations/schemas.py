"""Pydantic request/response shapes for the integrations module (doc 08 §3.6)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    Connection,
    ConnectionStatus,
    FieldMapping,
    IntegrationProviderKind,
    PushErrorCode,
    PushLog,
    PushStatus,
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
