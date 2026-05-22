"""Pydantic request/response shapes for the searches module (doc 06 §3, doc 08).

A saved search (H1) stores a validated subset of the G1 feed filter params
(``signals.list_workspace_signals``) so it can be re-run from the UI and, later,
drive a digest (H3). The :class:`SearchFilters` model is the **contract** for
that stored blob: it mirrors the feed query params and validates them up front so
the service / DB / digest runner can trust the persisted values.

The signal-type taxonomy and the feed status set are owned by the *signals*
module — we import them through ``signals.services`` (the only sanctioned
cross-module surface, doc 06 §3) rather than re-declaring them, so the saved
search can never reference a filter the feed cannot honour.

``SavedSearchCreate`` requires a name + filters; ``SavedSearchUpdate`` is a
partial patch (rename / re-filter / toggle sharing). ``SavedSearchOut`` is the
workspace-scoped public representation, and ``SavedSearchPage`` is the cursor
envelope mirroring the icp module's ``IcpPage`` (doc 06 §5, doc 08 §1.5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from civicsignals_api.modules.signals import services as signals_services

# The closed signal-type taxonomy and the valid feed status set, sourced from the
# signals module's public service surface so a saved filter can only reference
# values the feed accepts (doc 06 §3 — searches reaches signals only via services).
SignalType = signals_services.SignalType
_VALID_STATUSES: frozenset[str] = frozenset(signals_services.SCORE_STATUSES)

# Score is on the 0..100 scale (doc 14 §5.3 ``WorkspaceScore.score``).
MinScore = Annotated[float, Field(ge=0.0, le=100.0)]

_NAME = Field(min_length=1, max_length=200)


class SearchFilters(BaseModel):
    """The G1 feed filter subset a saved search captures (doc 14 §5.3, §8).

    Each field maps 1:1 to a ``signals.list_workspace_signals`` parameter:

    - ``signal_type`` — narrow to one signal-type slug (validated against the
      canonical taxonomy).
    - ``statuses`` — feed statuses to include (subset of ``SCORE_STATUSES``);
      ``None`` keeps the feed default (new/reviewed/pinned).
    - ``min_score`` — floor on the 0..100 workspace score.
    - ``published_at_gte`` / ``published_at_lt`` — bound the signal's
      ``occurred_at`` (doc 08 §1.6 date-range conventions).

    ``extra="forbid"`` means a stray/unknown filter key is rejected at the edge
    rather than silently persisted and ignored by the feed.

    # TODO H2: richer filter validation (keyword facets, entity / deal-band
    #   filters) extends this model as the feed query grows.
    """

    model_config = ConfigDict(extra="forbid")

    signal_type: SignalType | None = None
    statuses: list[str] | None = None
    min_score: MinScore | None = None
    published_at_gte: datetime | None = None
    published_at_lt: datetime | None = None

    @field_validator("statuses")
    @classmethod
    def _v_statuses(cls, value: list[str] | None) -> list[str] | None:
        """Reject unknown statuses and de-duplicate while preserving order."""
        if value is None:
            return None
        seen: set[str] = set()
        cleaned: list[str] = []
        for status in value:
            if status not in _VALID_STATUSES:
                raise ValueError(
                    f"unknown status {status!r}; valid: {', '.join(sorted(_VALID_STATUSES))}"
                )
            if status not in seen:
                seen.add(status)
                cleaned.append(status)
        return cleaned

    @model_validator(mode="after")
    def _v_date_range(self) -> SearchFilters:
        if (
            self.published_at_gte is not None
            and self.published_at_lt is not None
            and self.published_at_gte >= self.published_at_lt
        ):
            raise ValueError("published_at_gte must be before published_at_lt")
        return self

    def to_storage(self) -> dict[str, Any]:
        """Serialize to the JSONB blob persisted on the row.

        Drops ``None`` values (an absent filter == "no constraint") and renders
        the enum / datetimes as JSON-native scalars so the column round-trips
        without bespoke decoding.
        """
        return self.model_dump(mode="json", exclude_none=True)


class SavedSearchCreate(BaseModel):
    """Request body for ``POST /searches`` (H1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = _NAME
    filters: SearchFilters = Field(default_factory=SearchFilters)
    is_shared: bool = False


class SavedSearchUpdate(BaseModel):
    """Partial patch for ``PATCH /searches/{id}`` (rename / re-filter / share).

    Every field is optional; ``None`` (or absent) means "leave unchanged". A
    caller renames by sending ``name``, re-filters by sending ``filters`` (the
    whole filter set is replaced), and toggles sharing via ``is_shared``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    filters: SearchFilters | None = None
    is_shared: bool | None = None


class SavedSearchOut(BaseModel):
    """Workspace-scoped public representation of a saved search."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    created_by: UUID
    name: str
    filters: dict[str, Any]
    is_shared: bool
    created_at: datetime
    updated_at: datetime


class SavedSearchPage(BaseModel):
    """A cursor-paginated page of saved searches (doc 06 §5, doc 08 §1.5)."""

    items: list[SavedSearchOut]
    next_cursor: str | None = None
