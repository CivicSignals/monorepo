"""Pydantic request/response shapes for the entities module (doc 06 §3, doc 08 §1).

The entity directory is a *public read-only* surface (doc 07 §3 — entities are
global, not workspace-scoped), so these shapes carry no workspace fields. List
responses use **cursor** pagination (doc 06 §5, doc 08: ``?cursor=…&limit=25``,
never offset).
"""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class EntityKindRead(BaseModel):
    """One entity-kind taxonomy row."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    label: str
    category: str
    description: str | None = None


class GeoRead(BaseModel):
    """One geography row (state / county / place)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    level: str
    geo_id: str
    name: str
    country: str
    state: str | None = None
    state_fips: str | None = None
    county_fips: str | None = None
    place_fips: str | None = None


class EntityRead(BaseModel):
    """An entity as exposed on the public directory."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: str
    name: str
    short_name: str | None = None
    country: str
    state: str | None = None
    region: str | None = None
    kind_id: uuid.UUID | None = None
    geo_id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None
    nces_leaid: str | None = None
    ipeds_unitid: str | None = None
    census_gid: str | None = None
    population: int | None = None
    enrollment: int | None = None
    annual_budget_usd: float | None = None
    primary_website: str | None = None
    procurement_portal_url: str | None = None
    board_meeting_cadence: str | None = None
    attributes: dict[str, object] = Field(default_factory=dict)
    source_urls: list[str] = Field(default_factory=list)
    created_at: dt.datetime
    updated_at: dt.datetime


class EntityPage(BaseModel):
    """A cursor-paginated page of entities (doc 06 §5, doc 08).

    ``next_cursor`` is ``None`` on the last page; otherwise it is the opaque
    token the client passes back as ``?cursor=…`` to fetch the next page.
    """

    items: list[EntityRead]
    next_cursor: str | None = None
