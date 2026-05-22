"""Pydantic request/response shapes for the admin module (doc 06 §3).

B9 — audit log read API.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditEventOut(BaseModel):
    """Public representation of one audit event (append-only, read-only)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID | None = None
    actor_user_id: UUID | None = None
    actor_token_id: UUID | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    metadata: dict[str, Any] | None = None
    ip: str | None = None
    occurred_at: datetime

    @classmethod
    def from_orm_event(cls, event: Any) -> AuditEventOut:
        """Map the ``metadata_`` ORM column alias to the public ``metadata`` field."""
        return cls(
            id=event.id,
            workspace_id=event.workspace_id,
            actor_user_id=event.actor_user_id,
            actor_token_id=event.actor_token_id,
            action=event.action,
            target_type=event.target_type,
            target_id=event.target_id,
            metadata=event.metadata_,
            ip=event.ip,
            occurred_at=event.occurred_at,
        )


class AuditEventPage(BaseModel):
    """A cursor-paginated page of audit events (doc 06 §5, doc 08 §1.5).

    ``next_cursor`` is ``None`` on the last page; otherwise it is the opaque
    token the client passes back as ``?cursor=…`` to fetch the next page.
    """

    items: list[AuditEventOut]
    next_cursor: str | None = None
