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
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from civicsignals_api.modules.signals import services as signals_services

from .validation import (
    FilterValidationError,
    FilterViolation,
    assert_valid_filters,
    validate_filters,
)

# The closed signal-type taxonomy and the valid feed status set, sourced from the
# signals module's public service surface so a saved filter can only reference
# values the feed accepts (doc 06 §3 — searches reaches signals only via services).
SignalType = signals_services.SignalType

# Score is on the 0..100 scale (doc 14 §5.3 ``WorkspaceScore.score``); the actual
# range / type checks live in the centralized rule engine (``validation.py``) so
# the message is explicit, not Pydantic's generic constraint text.
MinScore = float

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

    Validation (H2) is delegated to the centralized rule engine in
    ``validation.py`` via the ``mode="before"`` validator below: unknown keys,
    unknown enum values, out-of-range scores, empty status selections and
    inverted date ranges each fail with an explicit, human-readable message
    (one per violated rule) — not Pydantic's generic constraint text.

    # TODO H3: a digest schedule reads this stored blob; richer filter facets
    #   (keyword / entity / deal-band) extend the rule set as the feed grows.
    """

    # Permissive at the Pydantic layer (``extra="allow"`` so unknown keys reach
    # the rule engine and are rejected with an explicit "not a filter" message
    # rather than Pydantic's generic "extra inputs not permitted"). The single
    # ``mode="before"`` validator runs the centralized H2 rule set, so every
    # invalid combination surfaces with a human-readable message.
    model_config = ConfigDict(extra="allow")

    signal_type: SignalType | None = None
    statuses: list[str] | None = None
    min_score: MinScore | None = None
    published_at_gte: datetime | None = None
    published_at_lt: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _v_rules(cls, data: Any) -> Any:
        """Run the centralized H2 rule engine before field coercion.

        Validating the *raw* mapping up front (rather than per-field afterward)
        means the explicit per-rule messages always win over Pydantic's generic
        coercion errors, and the rules stay in one place (``validation.py``)
        shared with the routes. Statuses are de-duplicated (order-preserving)
        once the values are known good.
        """
        if not isinstance(data, dict):
            return data
        violations = validate_filters(data)
        if violations:
            # Surface every violation; the route maps this to a 422 with one
            # ``errors[]`` entry per rule (RFC 7807, doc 08 §1.7).
            raise FilterValidationError(violations)
        statuses = data.get("statuses")
        if isinstance(statuses, list):
            seen: set[Any] = set()
            deduped: list[Any] = []
            for status in statuses:
                if status not in seen:
                    seen.add(status)
                    deduped.append(status)
            data = {**data, "statuses": deduped}
        return data

    def to_storage(self) -> dict[str, Any]:
        """Serialize to the JSONB blob persisted on the row.

        Drops ``None`` values (an absent filter == "no constraint") and renders
        the enum / datetimes as JSON-native scalars so the column round-trips
        without bespoke decoding.
        """
        return self.model_dump(mode="json", exclude_none=True)


class SavedSearchCreate(BaseModel):
    """Request body for ``POST /searches`` (H1).

    ``filters`` is accepted as the raw filter mapping (not the parsed
    :class:`SearchFilters`) so the route can validate it through the centralized
    H2 rule engine and emit **one RFC 7807 ``errors[]`` entry per violated rule**
    — Pydantic would otherwise collapse a nested-model failure into a single,
    generic message. :meth:`validated_filters` does the parse/normalize once the
    route has reported any rule violations.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = _NAME
    filters: dict[str, Any] = Field(default_factory=dict)
    is_shared: bool = False

    def validated_filters(self) -> SearchFilters:
        """Parse the raw filter blob into the storage contract (after rule checks).

        Raises :class:`FilterValidationError` for any violated rule so the route
        renders the full set of explicit messages.
        """
        return _parse_filters(self.filters)


class SavedSearchUpdate(BaseModel):
    """Partial patch for ``PATCH /searches/{id}`` (rename / re-filter / share).

    Every field is optional; ``None`` (or absent) means "leave unchanged". A
    caller renames by sending ``name``, re-filters by sending ``filters`` (the
    whole filter set is replaced), and toggles sharing via ``is_shared``.

    As with create, ``filters`` is the raw mapping; the route validates it via
    the H2 rule engine before persisting (see :meth:`validated_filters`).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    filters: dict[str, Any] | None = None
    is_shared: bool | None = None

    def validated_filters(self) -> SearchFilters | None:
        """Parse the raw filter blob, or ``None`` when ``filters`` was omitted."""
        if self.filters is None:
            return None
        return _parse_filters(self.filters)


def _parse_filters(raw: dict[str, Any]) -> SearchFilters:
    """Validate + normalize a raw filter mapping into :class:`SearchFilters`.

    Runs the centralized rule engine first (so the explicit per-rule messages are
    what a caller sees) and only then constructs the typed model. A clean blob
    that nonetheless trips a Pydantic coercion edge (e.g. a date string the rule
    engine parsed but the model cannot) is reported as a single ``filters``
    violation rather than leaking a raw Pydantic error.
    """
    assert_valid_filters(raw)
    try:
        return SearchFilters.model_validate(raw)
    except ValidationError as exc:  # pragma: no cover - rule engine covers the real cases
        raise FilterValidationError(
            [
                FilterViolation(
                    field="filters",
                    code="invalid_filters",
                    message=str(exc.errors()[0].get("msg", "Invalid filters.")),
                )
            ]
        ) from exc


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
