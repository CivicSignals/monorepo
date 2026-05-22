"""Pydantic request/response shapes for the integrations module (doc 08 §3.6)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    Connection,
    ConnectionStatus,
    IntegrationProviderKind,
    PushErrorCode,
    PushLog,
    PushStatus,
)

CONNECTION_NAME_MAX_LEN = 160


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
