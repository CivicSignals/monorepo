"""Pydantic request/response shapes for the notifications module (doc 06 §3).

H3 exposes a saved-search digest subscription: the per-(saved-search, user)
schedule (off / daily / weekly, send hour, recipient timezone). The upsert body
sets the calling member's own digest for one saved search; the response is the
stored row.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .digest import DigestFrequency, resolve_tz


class DigestSubscriptionUpsert(BaseModel):
    """Request body to set the caller's digest for a saved search (H3).

    ``frequency`` is the only required field; ``send_hour`` / ``weekday`` /
    ``timezone`` carry sensible defaults. ``timezone`` is validated as a real IANA
    name up front so a typo is a clean ``422`` rather than a silent UTC fallback at
    send time.
    """

    model_config = ConfigDict(extra="forbid")

    frequency: DigestFrequency
    send_hour: int = Field(default=8, ge=0, le=23)
    weekday: int = Field(default=0, ge=0, le=6)
    timezone: str = Field(default="UTC", min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        """Reject an unknown IANA tz name (resolve_tz would silently fall back)."""
        # resolve_tz never raises; compare against the canonical name to detect a
        # fallback. UTC is always valid.
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value


class DigestSubscriptionOut(BaseModel):
    """The stored digest subscription for one saved search + recipient."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    saved_search_id: UUID
    workspace_id: UUID
    user_id: UUID
    frequency: DigestFrequency
    send_hour: int
    weekday: int
    timezone: str
    last_sent_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


__all__ = [
    "DigestFrequency",
    "DigestSubscriptionOut",
    "DigestSubscriptionUpsert",
    "resolve_tz",
]
