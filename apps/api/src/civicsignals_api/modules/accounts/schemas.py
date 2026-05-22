"""Pydantic request/response shapes for the accounts module (doc 06 §3)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr


class UserOut(BaseModel):
    """Public representation of a user (doc 08 §3.1 ``user`` object)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    name: str | None = None
    email_verified: bool
    created_at: datetime
