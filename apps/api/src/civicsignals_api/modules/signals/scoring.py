# SPDX-License-Identifier: AGPL-3.0-only
"""Banded extraction-confidence scoring for the signals module (doc 19 §6.2-§6.3; E6).

This is the funnel's Stage-5 *score* step (doc 19 §1, §6): a candidate that passed
the strict per-type schema hard gate (E4, ``signals.schemas``) gets an overall
``extraction_confidence`` in [0, 1] from a **weighted blend** of five components
(doc 19 §6.2), and the score is mapped to one of four **bands** (doc 19 §6.3) that
decide how (or whether) the signal is surfaced.

The five components and their default weights (doc 19 §6.2):

============================  ======  ====================================
Component                     Weight  What it measures
============================  ======  ====================================
Field-level confidences        40%    Average confidence across extracted fields
LLM self-reported confidence   20%    The model's self-assessed certainty (if used)
Source quality                 15%    Recipe-configured source-quality tier
Schema completeness            15%    Fraction of *optional* fields also filled
Cross-validation               10%    Sanity checks (date plausible? amount > 0? entity resolved?)
============================  ======  ====================================

The four bands (doc 19 §6.3):

=========================  ====================================================
Band                       Action
=========================  ====================================================
``normal``    (≥ 0.8)      stored normally
``degraded``  (0.6-0.8)    stored with the ``degraded`` flag ("verify the details")
``pending_review`` (0.4-0.6) stored ``pending_review``; paid features gated until reviewed
``rejected``  (< 0.4)      candidate rejected; not surfaced (logged for analysis)
=========================  ====================================================

This module is **pure** — no DB, no I/O, no LLM calls — so the blend math and the
band boundaries are trivially unit-testable and importable from both the
``signals`` service and the ``extraction`` pipeline's ``score`` stage (cross-module
use goes through ``signals.services``, which re-exports the public surface here).

The thresholds and weights are **recipe-configurable** (doc 19 §6.3): an
authoritative source (a state portal's official API) can raise the floor, a noisy
news aggregator can lower it. The :class:`ConfidenceConfig` carries the override;
``DEFAULT_CONFIG`` is the documented §6.2/§6.3 baseline.

# TODO E6 (recipe plumbing): the per-recipe override is currently passed in by the
# caller (the extraction pipeline / a backfill). Reading it from the recipe YAML
# (a ``confidence:`` block validated by ``packages/recipe-schema``) is the deferred
# piece — the ``Recipe`` schema has no such block yet. :func:`config_from_recipe`
# is the seam: it reads a best-effort mapping today and is where the typed recipe
# field hooks in once it lands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final

from .schemas import PAYLOAD_BY_TYPE, SignalType

# --- Defaults (the documented §6.2 weights + §6.3 thresholds) ------------------

# The blend weights (doc 19 §6.2). They sum to 1.0; :class:`ConfidenceWeights`
# validates that a custom set still does (within a small epsilon).
DEFAULT_WEIGHT_FIELD_LEVEL: Final = 0.40
DEFAULT_WEIGHT_LLM_SELF_REPORT: Final = 0.20
DEFAULT_WEIGHT_SOURCE_QUALITY: Final = 0.15
DEFAULT_WEIGHT_SCHEMA_COMPLETENESS: Final = 0.15
DEFAULT_WEIGHT_CROSS_VALIDATION: Final = 0.10

# The band thresholds (doc 19 §6.3): ≥0.8 normal · 0.6-0.8 degraded ·
# 0.4-0.6 pending_review · <0.4 rejected.
DEFAULT_NORMAL_FLOOR: Final = 0.8
DEFAULT_DEGRADED_FLOOR: Final = 0.6
DEFAULT_PENDING_REVIEW_FLOOR: Final = 0.4

# Confidence is capped at 0.95 — a perfect 1.0 is suspicious (doc 19 §6.4: usually
# means the LLM was given leading context that biased it toward overconfidence).
CONFIDENCE_CAP: Final = 0.95

# A neutral mid value used as the default for any component we cannot observe (e.g.
# the LLM did not self-report, or the recipe configured no source-quality tier). It
# is deliberately a non-committal 0.5 so a missing signal neither inflates nor
# tanks the blend.
NEUTRAL: Final = 0.5

# How far in the future an event date may plausibly be before it reads as a likely
# extraction error (doc 19 §6.2 cross-validation: "date is plausible?"). RFP due
# dates, contract expiries, etc. can legitimately be a year or two out, but a date
# decades away is almost certainly a mis-parse.
MAX_PLAUSIBLE_FUTURE: Final = timedelta(days=730)
# Likewise, an event timestamp far in the past on a *freshly observed* document is
# suspicious. We keep this generous (historical board records exist) but flag the
# truly implausible.
MAX_PLAUSIBLE_PAST: Final = timedelta(days=365 * 25)

# Fields that exist on *every* payload (the base) and so are never counted toward a
# type's "optional fields filled" completeness fraction (doc 19 §6.2).
_BASE_FIELDS: Final = frozenset({"signal_type", "title", "summary"})

# Field-name substrings whose values we treat as dates for the cross-validation
# plausibility check.
_DATE_FIELD_HINTS: Final = ("_at", "_date", "date")
# Field-name substrings whose values we treat as monetary amounts (must be > 0).
_AMOUNT_FIELD_HINTS: Final = ("amount_cents", "_cents")


class ConfidenceBand(StrEnum):
    """The four confidence bands (doc 19 §6.3).

    ``normal`` — stored normally (≥ normal floor).
    ``degraded`` — stored with a "verify the details" flag (degraded..normal).
    ``pending_review`` — stored but held; paid actions gated (pending..degraded).
    ``rejected`` — not stored; logged for retrospective analysis (< pending floor).
    """

    NORMAL = "normal"
    DEGRADED = "degraded"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ConfidenceWeights:
    """The five blend weights (doc 19 §6.2). Defaults are the documented values.

    A custom set must still sum to ~1.0 (a recipe that reweights the blend keeps it
    a weighted *average*); :meth:`__post_init__` validates that and that each weight
    is non-negative, raising :class:`ValueError` on a malformed override.
    """

    field_level: float = DEFAULT_WEIGHT_FIELD_LEVEL
    llm_self_report: float = DEFAULT_WEIGHT_LLM_SELF_REPORT
    source_quality: float = DEFAULT_WEIGHT_SOURCE_QUALITY
    schema_completeness: float = DEFAULT_WEIGHT_SCHEMA_COMPLETENESS
    cross_validation: float = DEFAULT_WEIGHT_CROSS_VALIDATION

    def __post_init__(self) -> None:
        weights = (
            self.field_level,
            self.llm_self_report,
            self.source_quality,
            self.schema_completeness,
            self.cross_validation,
        )
        if any(w < 0 for w in weights):
            raise ValueError("confidence weights must be non-negative")
        total = sum(weights)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"confidence weights must sum to 1.0 (got {total})")


@dataclass(frozen=True, slots=True)
class BandThresholds:
    """The three band floors (doc 19 §6.3). Recipe-configurable (doc 19 §6.3).

    ``normal_floor`` ≥ ``degraded_floor`` ≥ ``pending_review_floor`` must hold (the
    bands are ordered); :meth:`__post_init__` validates the ordering and the [0, 1]
    range. An authoritative source raises the floors (≥0.7 baseline); a noisy
    aggregator lowers them (≥0.5).
    """

    normal_floor: float = DEFAULT_NORMAL_FLOOR
    degraded_floor: float = DEFAULT_DEGRADED_FLOOR
    pending_review_floor: float = DEFAULT_PENDING_REVIEW_FLOOR

    def __post_init__(self) -> None:
        floors = (self.normal_floor, self.degraded_floor, self.pending_review_floor)
        if not all(0.0 <= f <= 1.0 for f in floors):
            raise ValueError("band thresholds must be within [0, 1]")
        if not (self.normal_floor >= self.degraded_floor >= self.pending_review_floor):
            raise ValueError(
                "band thresholds must be ordered "
                "normal_floor >= degraded_floor >= pending_review_floor"
            )

    def band_for(self, score: float) -> ConfidenceBand:
        """Map a 0-1 score to its band (doc 19 §6.3).

        Floors are inclusive lower bounds: a score *at* the normal floor is
        ``normal`` (doc 19 §6.3's "≥ 0.8" is inclusive). Below the pending-review
        floor the candidate is ``rejected``.
        """
        if score >= self.normal_floor:
            return ConfidenceBand.NORMAL
        if score >= self.degraded_floor:
            return ConfidenceBand.DEGRADED
        if score >= self.pending_review_floor:
            return ConfidenceBand.PENDING_REVIEW
        return ConfidenceBand.REJECTED


@dataclass(frozen=True, slots=True)
class ConfidenceConfig:
    """The recipe-configurable scoring config (doc 19 §6.2-§6.3).

    Bundles the blend ``weights``, the band ``thresholds``, and the
    ``source_quality`` tier this source is assigned (doc 19 §6.2: "state portal =
    high, local newspaper aggregator = lower"). ``source_quality`` is in [0, 1];
    it defaults to :data:`NEUTRAL` so an un-tiered source neither helps nor hurts.
    """

    weights: ConfidenceWeights = field(default_factory=ConfidenceWeights)
    thresholds: BandThresholds = field(default_factory=BandThresholds)
    source_quality: float = NEUTRAL

    def __post_init__(self) -> None:
        if not 0.0 <= self.source_quality <= 1.0:
            raise ValueError("source_quality must be within [0, 1]")


#: The documented §6.2/§6.3 baseline used when a recipe provides no override.
DEFAULT_CONFIG: Final = ConfidenceConfig()


@dataclass(frozen=True, slots=True)
class ConfidenceComponents:
    """The five resolved component scores (each in [0, 1]) before weighting.

    Surfaced so a caller (the "inspect this signal" panel, PRD F6.4; the recipe
    scorecard, doc 19 §12.4) can show *why* a signal scored what it did, not just
    the blended number.
    """

    field_level: float
    llm_self_report: float
    source_quality: float
    schema_completeness: float
    cross_validation: float


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """The outcome of scoring one candidate (doc 19 §6.2-§6.3).

    ``score`` is the blended, capped extraction confidence in [0, 1]; ``band`` is the
    mapped action band; ``components`` is the per-component breakdown for inspection.
    ``rejected`` / ``degraded`` / ``review_required`` are the convenience flags the
    store step lifts onto the ``signals_signal`` row (doc 19 §6.3).
    """

    score: float
    band: ConfidenceBand
    components: ConfidenceComponents

    @property
    def rejected(self) -> bool:
        """Whether the candidate falls in the ``rejected`` band (not surfaced)."""
        return self.band is ConfidenceBand.REJECTED

    @property
    def degraded(self) -> bool:
        """Whether the signal carries the ``degraded`` flag (doc 19 §6.3)."""
        return self.band is ConfidenceBand.DEGRADED

    @property
    def review_required(self) -> bool:
        """Whether the signal is held for review (the ``pending_review`` band)."""
        return self.band is ConfidenceBand.PENDING_REVIEW


# ---------------------------------------------------------------------------
# Component scorers (each pure, each in [0, 1])
# ---------------------------------------------------------------------------


def _clamp_unit(value: float) -> float:
    """Clamp a float into [0, 1]."""
    return max(0.0, min(1.0, value))


def field_level_confidence(
    fields: Mapping[str, object],
    *,
    fallback: float | None = None,
) -> float:
    """Average confidence across extracted fields (doc 19 §6.2, 40% component).

    Each extracted field item carries its own ``confidence`` (doc 19 §4.2). The
    extract stage may surface those as a ``field_confidences`` mapping
    (``{field_name: 0-1}``) on the candidate's ``fields``; when present we average
    its values. When the per-field detail is absent (the permissive E1/E4 extract
    seam emits a single document-level confidence), we fall back to ``fallback``
    (the candidate's self-reported confidence) and finally to :data:`NEUTRAL`.

    # TODO E4: once the deterministic Stage-3 first pass (spaCy NER + regex) and the
    # LLM-assisted pass populate true per-field confidences (doc 19 §4.2), this reads
    # them directly instead of falling back to the document-level value.
    """
    per_field = fields.get("field_confidences")
    values: list[float] = []
    if isinstance(per_field, dict):
        for raw in per_field.values():
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                values.append(_clamp_unit(float(raw)))
    if values:
        return sum(values) / len(values)
    if fallback is not None:
        return _clamp_unit(fallback)
    return NEUTRAL


def schema_completeness(signal_type: SignalType, fields: Mapping[str, object]) -> float:
    """Fraction of *optional* fields also filled for the type (doc 19 §6.2, 15%).

    Introspects the strict per-type payload (E4, ``signals.schemas``): the optional
    fields are those that are not required and not part of the always-present base
    (``signal_type``/``title``/``summary``). A field counts as "filled" when the
    candidate supplies a non-empty value for it. A type with **no** optional fields
    is treated as fully complete (1.0) — there is nothing more to fill, so the
    component should not penalise it.
    """
    model_cls = PAYLOAD_BY_TYPE[signal_type]
    optional_names = [
        name
        for name, info in model_cls.model_fields.items()
        if name not in _BASE_FIELDS and not info.is_required()
    ]
    if not optional_names:
        return 1.0
    filled = sum(1 for name in optional_names if _is_filled(fields.get(name)))
    return filled / len(optional_names)


def _is_filled(value: object) -> bool:
    """Whether a candidate field value counts as populated (non-empty)."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def cross_validation_score(
    fields: Mapping[str, object],
    *,
    entity_resolved: bool,
    now: datetime | None = None,
) -> float:
    """Sanity-check agreement across the candidate's fields (doc 19 §6.2, 10%).

    Three documented checks (doc 19 §6.2): the event date is plausible, every
    monetary amount is > 0, and the entity resolved. Each check that *applies*
    (the field is present) contributes equally; the component is the fraction that
    pass. The entity-resolution check always applies. When no date/amount field is
    present, only the entity check counts — a candidate cannot be penalised for a
    check that does not apply to its type.
    """
    now = now or datetime.now(UTC)
    passed = 0
    total = 0

    for name, value in fields.items():
        lowered = name.lower()
        if any(hint in lowered for hint in _AMOUNT_FIELD_HINTS):
            total += 1
            if _amount_is_positive(value):
                passed += 1
        elif any(lowered.endswith(h) or h == lowered for h in _DATE_FIELD_HINTS):
            total += 1
            if _date_is_plausible(value, now):
                passed += 1

    # Entity resolution always applies (doc 19 §4.3, §6.2).
    total += 1
    if entity_resolved:
        passed += 1

    return passed / total if total else NEUTRAL


def _amount_is_positive(value: object) -> bool:
    """Whether a monetary amount field is a number > 0 (doc 19 §6.2)."""
    if isinstance(value, bool):  # bool is an int subclass; never a valid amount
        return False
    if isinstance(value, (int, float)):
        return value > 0
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").replace("$", "").strip()) > 0
        except ValueError:
            return False
    return False


def _date_is_plausible(value: object, now: datetime) -> bool:
    """Whether a date/datetime field is within the plausible window (doc 19 §6.2)."""
    parsed = _coerce_datetime(value)
    if parsed is None:
        # A present-but-unparseable date is implausible (likely a mis-extraction).
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if parsed > now + MAX_PLAUSIBLE_FUTURE:
        return False
    return parsed >= now - MAX_PLAUSIBLE_PAST


def _coerce_datetime(value: object) -> datetime | None:
    """Best-effort coerce a candidate date value to a ``datetime`` (None if not)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Accept ISO-8601 with a trailing Z (datetime.fromisoformat handles Z only
        # on 3.11+, but normalise defensively).
        normalised = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            return datetime.fromisoformat(normalised)
        except ValueError:
            try:
                return datetime.combine(date.fromisoformat(text), datetime.min.time(), tzinfo=UTC)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# The blend
# ---------------------------------------------------------------------------


def blend(components: ConfidenceComponents, weights: ConfidenceWeights) -> float:
    """Weighted blend of the five components, capped at 0.95 (doc 19 §6.2, §6.4).

    The weights sum to 1.0 (validated on :class:`ConfidenceWeights`), so the blend is
    a weighted average already in [0, 1]; we still clamp defensively, then cap at
    :data:`CONFIDENCE_CAP` because a perfect score is suspicious (doc 19 §6.4).
    """
    raw = (
        components.field_level * weights.field_level
        + components.llm_self_report * weights.llm_self_report
        + components.source_quality * weights.source_quality
        + components.schema_completeness * weights.schema_completeness
        + components.cross_validation * weights.cross_validation
    )
    return min(_clamp_unit(raw), CONFIDENCE_CAP)


def score_candidate_confidence(
    signal_type: SignalType,
    fields: Mapping[str, object],
    *,
    llm_confidence: float | None = None,
    entity_resolved: bool = False,
    config: ConfidenceConfig = DEFAULT_CONFIG,
    now: datetime | None = None,
) -> ScoreResult:
    """Compute the banded extraction confidence for one candidate (doc 19 §6.2-§6.3).

    Resolves the five components from the candidate's typed ``signal_type`` + raw
    ``fields`` plus the out-of-band inputs:

    - **field-level** — averaged ``field_confidences`` if present, else the
      ``llm_confidence`` document-level value (doc 19 §6.2);
    - **llm self-report** — ``llm_confidence`` (NEUTRAL when the LLM did not report);
    - **source quality** — ``config.source_quality`` (the recipe tier, doc 19 §6.2);
    - **schema completeness** — fraction of optional fields filled (doc 19 §6.2);
    - **cross-validation** — date/amount/entity sanity checks (doc 19 §6.2).

    Returns a :class:`ScoreResult` carrying the blended score, the mapped band, and
    the per-component breakdown. The caller (the extraction store step) acts on the
    band: ``rejected`` is dropped, ``degraded``/``pending_review`` set the row flags.
    """
    components = ConfidenceComponents(
        field_level=field_level_confidence(fields, fallback=llm_confidence),
        llm_self_report=_clamp_unit(llm_confidence) if llm_confidence is not None else NEUTRAL,
        source_quality=config.source_quality,
        schema_completeness=schema_completeness(signal_type, fields),
        cross_validation=cross_validation_score(fields, entity_resolved=entity_resolved, now=now),
    )
    score = blend(components, config.weights)
    band = config.thresholds.band_for(score)
    return ScoreResult(score=score, band=band, components=components)


def config_from_recipe(recipe_confidence: Mapping[str, object] | None) -> ConfidenceConfig:
    """Build a :class:`ConfidenceConfig` from a recipe's (optional) ``confidence`` block.

    Recipe-configurable thresholds + source quality (doc 19 §6.3). A recipe may
    override any subset of: the band floors (``normal_floor``/``degraded_floor``/
    ``pending_review_floor``), the source-quality tier (``source_quality``), and the
    blend weights (``weights: {...}``). Anything absent falls back to the documented
    §6.2/§6.3 default. An out-of-range / malformed override raises ``ValueError``
    (via the dataclass validators) — a misconfigured recipe is a hard error, not a
    silent fallback.

    # TODO E6 (recipe plumbing): the ``Recipe`` schema (recipes module +
    # packages/recipe-schema) has no typed ``confidence:`` block yet. This reads a
    # best-effort untyped mapping so the override mechanism works today (a backfill /
    # test can pass one); the typed recipe field + JSON Schema is the deferred piece
    # that will feed this function its input.
    """
    if not recipe_confidence:
        return DEFAULT_CONFIG

    thresholds = BandThresholds(
        normal_floor=_get_float(recipe_confidence, "normal_floor", DEFAULT_NORMAL_FLOOR),
        degraded_floor=_get_float(recipe_confidence, "degraded_floor", DEFAULT_DEGRADED_FLOOR),
        pending_review_floor=_get_float(
            recipe_confidence, "pending_review_floor", DEFAULT_PENDING_REVIEW_FLOOR
        ),
    )
    source_quality = _get_float(recipe_confidence, "source_quality", NEUTRAL)

    raw_weights = recipe_confidence.get("weights")
    if isinstance(raw_weights, dict):
        weights = ConfidenceWeights(
            field_level=_get_float(raw_weights, "field_level", DEFAULT_WEIGHT_FIELD_LEVEL),
            llm_self_report=_get_float(
                raw_weights, "llm_self_report", DEFAULT_WEIGHT_LLM_SELF_REPORT
            ),
            source_quality=_get_float(raw_weights, "source_quality", DEFAULT_WEIGHT_SOURCE_QUALITY),
            schema_completeness=_get_float(
                raw_weights, "schema_completeness", DEFAULT_WEIGHT_SCHEMA_COMPLETENESS
            ),
            cross_validation=_get_float(
                raw_weights, "cross_validation", DEFAULT_WEIGHT_CROSS_VALIDATION
            ),
        )
    else:
        weights = ConfidenceWeights()

    return ConfidenceConfig(weights=weights, thresholds=thresholds, source_quality=source_quality)


def _get_float(mapping: Mapping[str, object], key: str, default: float) -> float:
    """Read ``key`` from ``mapping`` as a float, falling back to ``default``.

    A present-but-non-numeric value raises ``ValueError`` — a malformed override
    must not silently fall back to the default (doc 19 §6.3 thresholds are safety
    gates).
    """
    if key not in mapping:
        return default
    raw = mapping[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"confidence override {key!r} must be a number, got {raw!r}")
    return float(raw)


__all__ = [
    "CONFIDENCE_CAP",
    "DEFAULT_CONFIG",
    "NEUTRAL",
    "BandThresholds",
    "ConfidenceBand",
    "ConfidenceComponents",
    "ConfidenceConfig",
    "ConfidenceWeights",
    "ScoreResult",
    "blend",
    "config_from_recipe",
    "cross_validation_score",
    "field_level_confidence",
    "schema_completeness",
    "score_candidate_confidence",
]
