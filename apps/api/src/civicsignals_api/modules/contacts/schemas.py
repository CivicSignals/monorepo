"""Pydantic request/response shapes for the contacts module (doc 06 §3, doc 08, C2).

Contacts are **global**, not workspace-scoped (doc 07 §3). These schemas carry
no ``workspace_id`` field. List responses use **cursor** pagination (doc 06 §5,
doc 08: ``?cursor=…&limit=25``, never offset).

Per-record provenance fields are included in read schemas so callers can trace
WHERE contact data came from (doc 16 §18).
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ProvenanceRead(BaseModel):
    """Source provenance embedded in every contact record (doc 16 §18)."""

    model_config = ConfigDict(from_attributes=True)

    source: str | None = None
    source_url: str | None = None
    source_recipe_id: uuid.UUID | None = None
    confidence: float | None = None
    observed_at: dt.datetime | None = None
    verified: bool = False
    last_verified_at: dt.datetime | None = None


class ContactEmailRead(BaseModel):
    """An email record for a contact."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    email: str
    is_primary: bool
    email_status: str
    # Provenance
    source: str | None = None
    source_url: str | None = None
    source_recipe_id: uuid.UUID | None = None
    confidence: float | None = None
    observed_at: dt.datetime | None = None
    verified: bool = False
    last_verified_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ContactPhoneRead(BaseModel):
    """A phone number record for a contact."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    phone: str
    phone_type: str | None = None
    is_primary: bool
    # Provenance
    source: str | None = None
    source_url: str | None = None
    source_recipe_id: uuid.UUID | None = None
    confidence: float | None = None
    observed_at: dt.datetime | None = None
    verified: bool = False
    last_verified_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ContactTitleRead(BaseModel):
    """A title/position history row for a contact."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    title: str
    department: str | None = None
    is_current: bool
    first_observed_at: dt.datetime | None = None
    last_observed_at: dt.datetime | None = None
    # Provenance
    source: str | None = None
    source_url: str | None = None
    source_recipe_id: uuid.UUID | None = None
    confidence: float | None = None
    observed_at: dt.datetime | None = None
    verified: bool = False
    last_verified_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ContactRead(BaseModel):
    """A contact as exposed on the public directory (global, not workspace-scoped)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_id: uuid.UUID
    name: str
    department: str | None = None
    title: str | None = None
    status: str
    canonical_email: str | None = None
    attributes: dict[str, object] = Field(default_factory=dict)
    # Per-record source provenance (doc 16 §18).
    source: str | None = None
    source_url: str | None = None
    source_recipe_id: uuid.UUID | None = None
    confidence: float | None = None
    observed_at: dt.datetime | None = None
    verified: bool = False
    last_verified_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ContactPage(BaseModel):
    """A cursor-paginated page of contacts (doc 06 §5, doc 08).

    ``next_cursor`` is ``None`` on the last page; otherwise it is the opaque
    token the client passes back as ``?cursor=…`` to fetch the next page.
    """

    items: list[ContactRead]
    next_cursor: str | None = None
