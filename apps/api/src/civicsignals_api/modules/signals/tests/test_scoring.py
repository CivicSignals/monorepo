# SPDX-License-Identifier: AGPL-3.0-only
"""Unit tests for the banded confidence scorer (E6; doc 19 §6.2-§6.3).

Pure tests — no DB, no I/O — covering:

- the weighted blend math (known inputs → expected score, doc 19 §6.2);
- the band boundaries (0.8 / 0.6 / 0.4 inclusive lower edges, doc 19 §6.3);
- the schema-completeness + cross-validation components;
- the 0.95 cap on a "too good" score (doc 19 §6.4);
- recipe-configurable threshold + weight overrides (doc 19 §6.3) and their
  validation (out-of-range / unordered / non-summing → ValueError).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from civicsignals_api.modules.signals.schemas import SignalType
from civicsignals_api.modules.signals.scoring import (
    CONFIDENCE_CAP,
    NEUTRAL,
    BandThresholds,
    ConfidenceBand,
    ConfidenceComponents,
    ConfidenceConfig,
    ConfidenceWeights,
    blend,
    config_from_recipe,
    cross_validation_score,
    field_level_confidence,
    schema_completeness,
    score_candidate_confidence,
)

# A fixed "now" so the date-plausibility checks are deterministic.
_NOW = datetime(2026, 5, 22, tzinfo=UTC)


# --- the blend math -------------------------------------------------------


def test_blend_weighted_average_of_known_components() -> None:
    """Known component values → the documented §6.2 weighted blend."""
    components = ConfidenceComponents(
        field_level=0.8,
        llm_self_report=0.8,
        source_quality=0.5,
        schema_completeness=0.0,
        cross_validation=0.5,
    )
    # 0.8*0.40 + 0.8*0.20 + 0.5*0.15 + 0.0*0.15 + 0.5*0.10 = 0.605
    assert blend(components, ConfidenceWeights()) == pytest.approx(0.605)


def test_blend_all_ones_is_capped_at_0_95() -> None:
    """A perfect blend is suspicious; capped at 0.95 (doc 19 §6.4)."""
    components = ConfidenceComponents(1.0, 1.0, 1.0, 1.0, 1.0)
    assert blend(components, ConfidenceWeights()) == pytest.approx(CONFIDENCE_CAP)


def test_blend_all_zero_is_zero() -> None:
    components = ConfidenceComponents(0.0, 0.0, 0.0, 0.0, 0.0)
    assert blend(components, ConfidenceWeights()) == pytest.approx(0.0)


# --- band boundaries (doc 19 §6.3) ----------------------------------------


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0.95, ConfidenceBand.NORMAL),
        (0.80, ConfidenceBand.NORMAL),  # 0.8 inclusive -> normal (doc 19 §6.3 "≥ 0.8")
        (0.7999, ConfidenceBand.DEGRADED),
        (0.60, ConfidenceBand.DEGRADED),  # 0.6 inclusive -> degraded
        (0.5999, ConfidenceBand.PENDING_REVIEW),
        (0.40, ConfidenceBand.PENDING_REVIEW),  # 0.4 inclusive -> pending_review
        (0.3999, ConfidenceBand.REJECTED),
        (0.0, ConfidenceBand.REJECTED),
    ],
)
def test_band_boundaries(score: float, expected: ConfidenceBand) -> None:
    assert BandThresholds().band_for(score) is expected


# --- field-level component ------------------------------------------------


def test_field_level_averages_per_field_confidences() -> None:
    fields = {"field_confidences": {"title": 0.9, "due_at": 0.7}}
    assert field_level_confidence(fields) == pytest.approx(0.8)


def test_field_level_falls_back_to_document_confidence() -> None:
    assert field_level_confidence({}, fallback=0.6) == pytest.approx(0.6)


def test_field_level_defaults_neutral_when_nothing_known() -> None:
    assert field_level_confidence({}) == pytest.approx(NEUTRAL)


def test_field_level_ignores_non_numeric_and_bools() -> None:
    fields = {"field_confidences": {"a": 0.8, "b": "high", "c": True}}
    # Only the numeric 0.8 counts (bool/str are skipped).
    assert field_level_confidence(fields) == pytest.approx(0.8)


# --- schema completeness --------------------------------------------------


def test_schema_completeness_fraction_of_optional_filled() -> None:
    # rfp_posted has 6 optional fields (rfp_number, amount_cents, posting_agency,
    # contact_name, submission_url, requirements). Fill 3.
    fields = {
        "rfp_number": "RFP-1",
        "amount_cents": 100,
        "posting_agency": "City",
    }
    assert schema_completeness(SignalType.RFP_POSTED, fields) == pytest.approx(3 / 6)


def test_schema_completeness_empty_values_dont_count() -> None:
    fields: dict[str, object] = {"rfp_number": "", "requirements": [], "amount_cents": None}
    assert schema_completeness(SignalType.RFP_POSTED, fields) == pytest.approx(0.0)


def test_schema_completeness_full() -> None:
    fields = {
        "rfp_number": "RFP-1",
        "amount_cents": 100,
        "posting_agency": "City",
        "contact_name": "A",
        "submission_url": "http://x",
        "requirements": ["a"],
    }
    assert schema_completeness(SignalType.RFP_POSTED, fields) == pytest.approx(1.0)


# --- cross-validation -----------------------------------------------------


def test_cross_validation_all_pass() -> None:
    fields = {"due_at": "2026-06-01T00:00:00Z", "amount_cents": 5000}
    # date plausible + amount > 0 + entity resolved -> 3/3.
    assert cross_validation_score(fields, entity_resolved=True, now=_NOW) == pytest.approx(1.0)


def test_cross_validation_implausible_date_and_zero_amount() -> None:
    fields = {"due_at": "3500-01-01T00:00:00Z", "amount_cents": 0}
    # date far future -> fail; amount 0 -> fail; entity unresolved -> fail. 0/3.
    assert cross_validation_score(fields, entity_resolved=False, now=_NOW) == pytest.approx(0.0)


def test_cross_validation_only_entity_check_applies() -> None:
    # No date/amount field -> only the entity-resolution check counts.
    assert cross_validation_score({}, entity_resolved=True, now=_NOW) == pytest.approx(1.0)
    assert cross_validation_score({}, entity_resolved=False, now=_NOW) == pytest.approx(0.0)


def test_cross_validation_unparseable_date_fails() -> None:
    fields = {"effective_date": "not-a-date"}
    # present-but-unparseable date is implausible; entity unresolved. 0/2.
    assert cross_validation_score(fields, entity_resolved=False, now=_NOW) == pytest.approx(0.0)


# --- end-to-end score_candidate_confidence --------------------------------


def test_score_candidate_confidence_degraded_band() -> None:
    result = score_candidate_confidence(
        SignalType.RFP_POSTED,
        {"title": "ERP RFP", "summary": "x", "due_at": "2026-06-01T17:00:00Z"},
        llm_confidence=0.8,
        entity_resolved=False,
        now=_NOW,
    )
    assert result.score == pytest.approx(0.605)
    assert result.band is ConfidenceBand.DEGRADED
    assert result.degraded is True
    assert result.review_required is False
    assert result.rejected is False


def test_score_candidate_confidence_high_resolved_is_normal() -> None:
    result = score_candidate_confidence(
        SignalType.RFP_POSTED,
        {
            "title": "ERP RFP",
            "summary": "x",
            "due_at": "2026-06-01T17:00:00Z",
            "rfp_number": "RFP-1",
            "amount_cents": 100,
            "posting_agency": "City",
            "contact_name": "A",
            "submission_url": "http://x",
            "requirements": ["a"],
        },
        llm_confidence=0.9,
        entity_resolved=True,
        config=ConfidenceConfig(source_quality=0.9),
        now=_NOW,
    )
    # 0.9*0.40 + 0.9*0.20 + 0.9*0.15 + 1.0*0.15 + 1.0*0.10 = 0.925 -> normal band.
    assert result.score == pytest.approx(0.925)
    assert result.band is ConfidenceBand.NORMAL


def test_score_candidate_confidence_rejected_band() -> None:
    result = score_candidate_confidence(
        SignalType.LEADERSHIP_CHANGE,
        {"title": "x", "summary": "y", "role": "CIO", "person_name": "A. Doe"},
        llm_confidence=0.1,
        entity_resolved=False,
        now=_NOW,
    )
    assert result.band is ConfidenceBand.REJECTED
    assert result.rejected is True


# --- recipe-configurable thresholds (doc 19 §6.3) -------------------------


def test_recipe_override_lowers_floor() -> None:
    cfg = config_from_recipe(
        {"normal_floor": 0.6, "degraded_floor": 0.4, "pending_review_floor": 0.2}
    )
    # A 0.605 blend that was "degraded" under defaults is now "normal".
    result = score_candidate_confidence(
        SignalType.RFP_POSTED,
        {"title": "ERP RFP", "summary": "x", "due_at": "2026-06-01T17:00:00Z"},
        llm_confidence=0.8,
        entity_resolved=False,
        config=cfg,
        now=_NOW,
    )
    assert result.score == pytest.approx(0.605)
    assert result.band is ConfidenceBand.NORMAL


def test_recipe_override_source_quality_and_weights() -> None:
    cfg = config_from_recipe(
        {
            "source_quality": 1.0,
            "weights": {
                "field_level": 0.2,
                "llm_self_report": 0.2,
                "source_quality": 0.4,
                "schema_completeness": 0.1,
                "cross_validation": 0.1,
            },
        }
    )
    assert cfg.source_quality == pytest.approx(1.0)
    assert cfg.weights.source_quality == pytest.approx(0.4)


def test_config_from_recipe_none_is_default() -> None:
    cfg = config_from_recipe(None)
    assert cfg.source_quality == pytest.approx(NEUTRAL)
    assert cfg.thresholds.normal_floor == pytest.approx(0.8)


# --- validation -----------------------------------------------------------


def test_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="must sum to"):
        ConfidenceWeights(field_level=0.5, llm_self_report=0.5)


def test_weights_must_be_non_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        ConfidenceWeights(field_level=-0.1, llm_self_report=0.3, cross_validation=0.8)


def test_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValueError, match="ordered"):
        BandThresholds(normal_floor=0.4, degraded_floor=0.6, pending_review_floor=0.2)


def test_thresholds_must_be_in_range() -> None:
    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        BandThresholds(normal_floor=1.5)


def test_source_quality_must_be_in_range() -> None:
    with pytest.raises(ValueError, match="source_quality"):
        ConfidenceConfig(source_quality=1.5)


def test_malformed_override_value_raises() -> None:
    with pytest.raises(ValueError, match="must be a number"):
        config_from_recipe({"normal_floor": "high"})
