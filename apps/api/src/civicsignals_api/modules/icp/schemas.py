"""Pydantic request/response shapes for the icp module (doc 06 §3, doc 08).

The ICP is the workspace's targeting config (doc 14 §3.1). These schemas validate
the dimensions and the per-signal-type weight map before they reach the service /
DB so the matcher (F3) can rely on clean, pre-indexed values (doc 14 §6).

``IcpCreate`` requires the caller to spell out the targeting; ``IcpUpdate`` is a
partial patch (every field optional, ``None`` = "leave unchanged"). ``IcpOut`` is
the workspace-scoped public representation. Lists are cursor-paginated (doc 06 §5,
doc 08 §1.5) — the envelope mirrors the accounts module's ``WorkspacePage``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class SignalType(StrEnum):
    """Signal types the system produces (doc 08 §3.2, §4 OpenAPI ``Signal.enum``).

    Used both for the ICP's ``signal_types`` (pre-filter) and as the allowed keys
    of the ``signal_weights`` map (scorer). Kept as the single source of truth for
    ICP validation; extending the catalogue is an additive, non-breaking change
    (doc 08 §1.2).
    """

    RFP_POSTED = "rfp_posted"
    BUDGET_DRAFTED = "budget_drafted"
    PERSONNEL_CHANGE = "personnel_change"
    GRANT_AWARDED = "grant_awarded"
    BOARD_DECISION = "board_decision"
    NEWS_MENTION = "news_mention"


class EntityKind(StrEnum):
    """Government entity kinds the ICP can target (doc 08 §3.2 ``entity_kind``)."""

    K12_DISTRICT = "k12_district"
    COMMUNITY_COLLEGE = "community_college"
    UNIVERSITY = "university"
    CITY = "city"
    COUNTY = "county"
    STATE_AGENCY = "state_agency"
    SPECIAL_DISTRICT = "special_district"


# Score threshold is on the 0..100 integer scale (doc 07 §icp ``threshold``).
Threshold = Annotated[int, Field(ge=0, le=100)]
# Per-signal-type weight is a 0..1 float (doc 14 §3.1 ``custom_weights``).
Weight = Annotated[float, Field(ge=0.0, le=1.0)]

_NAME = Field(min_length=1, max_length=200)


def _normalize_country(value: str) -> str:
    """Uppercase + validate an ISO 3166-1 alpha-2 country code (doc 08 §1.1)."""
    code = value.strip().upper()
    if len(code) != 2 or not code.isalpha():
        raise ValueError(f"country must be a 2-letter ISO code, got {value!r}")
    return code


def _normalize_state(value: str) -> str:
    """Uppercase a US state / region code (doc 14 §3.1 ``states``)."""
    code = value.strip().upper()
    if not (2 <= len(code) <= 2) or not code.isalpha():
        raise ValueError(f"state must be a 2-letter code, got {value!r}")
    return code


class _IcpDimensionsMixin:
    """Shared field validators for the ICP dimension arrays + weight map."""

    @field_validator("countries", check_fields=False)
    @classmethod
    def _v_countries(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [_normalize_country(c) for c in value]

    @field_validator("states", check_fields=False)
    @classmethod
    def _v_states(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return [_normalize_state(s) for s in value]

    @field_validator("keywords_required", "keywords_excluded", check_fields=False)
    @classmethod
    def _v_keywords(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = [kw.strip() for kw in value if kw.strip()]
        return cleaned

    @field_validator("signal_weights", check_fields=False)
    @classmethod
    def _v_weights(cls, value: dict[str, float] | None) -> dict[str, float] | None:
        """Keys must be known signal types; values must be in [0, 1]."""
        if value is None:
            return None
        for key, weight in value.items():
            if key not in SignalType.__members__.values():
                raise ValueError(f"unknown signal_type in signal_weights: {key!r}")
            if not (0.0 <= float(weight) <= 1.0):
                raise ValueError(f"weight for {key!r} must be in [0, 1], got {weight!r}")
        return {k: float(v) for k, v in value.items()}


def _check_ranges(
    min_size: int | None,
    max_size: int | None,
    deal_min: int | None,
    deal_max: int | None,
) -> None:
    """Reject inverted size / deal bands (shared by create + update)."""
    if min_size is not None and min_size < 0:
        raise ValueError("min_size must be >= 0")
    if max_size is not None and max_size < 0:
        raise ValueError("max_size must be >= 0")
    if min_size is not None and max_size is not None and min_size > max_size:
        raise ValueError("min_size must be <= max_size")
    if deal_min is not None and deal_min < 0:
        raise ValueError("deal_band_min_cents must be >= 0")
    if deal_max is not None and deal_max < 0:
        raise ValueError("deal_band_max_cents must be >= 0")
    if deal_min is not None and deal_max is not None and deal_min > deal_max:
        raise ValueError("deal_band_min_cents must be <= deal_band_max_cents")


class IcpCreate(_IcpDimensionsMixin, BaseModel):
    """Request body for ``POST /icp`` (doc 14 §3.1).

    Dimension arrays default to empty (= "all values", doc 14 §6.1). ``signal_types``
    and ``entity_kinds`` are typed enums so unknown values are rejected with ``422``.
    """

    name: str = _NAME
    countries: list[str] = Field(default_factory=lambda: ["US"])
    states: list[str] = Field(default_factory=list)
    entity_kinds: list[EntityKind] = Field(default_factory=list)
    signal_types: list[SignalType] = Field(default_factory=list)
    min_size: int | None = None
    max_size: int | None = None
    signal_weights: dict[str, Weight] = Field(default_factory=dict)
    keywords_required: list[str] = Field(default_factory=list)
    keywords_excluded: list[str] = Field(default_factory=list)
    deal_band_min_cents: int | None = None
    deal_band_max_cents: int | None = None
    threshold: Threshold = 50
    is_active: bool = True

    @model_validator(mode="after")
    def _v_ranges(self) -> IcpCreate:
        _check_ranges(
            self.min_size, self.max_size, self.deal_band_min_cents, self.deal_band_max_cents
        )
        return self


class IcpUpdate(_IcpDimensionsMixin, BaseModel):
    """Partial patch for ``PATCH /icp/{id}``.

    Every field is optional; ``None`` means "leave unchanged". ``is_active`` is
    intentionally **not** patchable here — toggling activation goes through the
    dedicated ``POST /icp/{id}/activate`` endpoint so the one-active-per-workspace
    invariant is enforced atomically.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    countries: list[str] | None = None
    states: list[str] | None = None
    entity_kinds: list[EntityKind] | None = None
    signal_types: list[SignalType] | None = None
    min_size: int | None = None
    max_size: int | None = None
    signal_weights: dict[str, Weight] | None = None
    keywords_required: list[str] | None = None
    keywords_excluded: list[str] | None = None
    deal_band_min_cents: int | None = None
    deal_band_max_cents: int | None = None
    threshold: Threshold | None = None

    @model_validator(mode="after")
    def _v_ranges(self) -> IcpUpdate:
        # Range checks only apply to the bounds present in this patch; mixed
        # cases (patching one bound) are reconciled against the stored row in the
        # service layer.
        _check_ranges(
            self.min_size, self.max_size, self.deal_band_min_cents, self.deal_band_max_cents
        )
        return self


class IcpOut(BaseModel):
    """Workspace-scoped public representation of an ICP definition."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    name: str
    countries: list[str]
    states: list[str]
    entity_kinds: list[str]
    signal_types: list[str]
    min_size: int | None
    max_size: int | None
    signal_weights: dict[str, float]
    keywords_required: list[str]
    keywords_excluded: list[str]
    deal_band_min_cents: int | None
    deal_band_max_cents: int | None
    threshold: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class IcpPage(BaseModel):
    """A cursor-paginated page of ICP definitions (doc 06 §5, doc 08 §1.5)."""

    items: list[IcpOut]
    next_cursor: str | None = None
