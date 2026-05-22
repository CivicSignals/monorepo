# SPDX-License-Identifier: AGPL-3.0-only
"""Pure unit tests for the F3 matcher predicate + scorer math (doc 14 §6).

These exercise ``signals.workspace_scoring`` with no DB — the matcher's
"could this signal match this ICP?" predicate (doc 14 §6.1), the component blend +
breakdown (doc 14 §6.2), the keyword required/excluded gate, recency decay, semantic
similarity, and the threshold gate. The DB-bound matcher query, fan-out, and sparse
upsert are covered separately in ``test_workspace_score_db.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from civicsignals_api.modules.signals.workspace_scoring import (
    ComponentWeights,
    IcpCriteria,
    KeywordExcludedError,
    ScoringConfig,
    SignalDimensions,
    keyword_match,
    recency_score,
    score_signal_against_icp,
    semantic_similarity,
    signal_matches_icp,
)

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


def _icp(**kw: object) -> IcpCriteria:
    base: dict[str, object] = {
        "signal_types": ("rfp_posted", "grant_awarded"),
        "countries": ("US",),
        "states": ("WA", "OR"),
        "entity_kinds": ("school_district",),
        "min_size": 2000,
        "max_size": 100000,
        "signal_weights": {"rfp_posted": 1.0, "grant_awarded": 0.8},
        "keywords_required": ("productivity software",),
        "keywords_excluded": ("athletics",),
        "threshold": 50.0,
    }
    base.update(kw)
    return IcpCriteria(**base)  # type: ignore[arg-type]


def _signal(**kw: object) -> SignalDimensions:
    base: dict[str, object] = {
        "signal_type": "rfp_posted",
        "country": "US",
        "state": "WA",
        "entity_kind": "school_district",
        "size": 23400,
    }
    base.update(kw)
    return SignalDimensions(**base)  # type: ignore[arg-type]


# --- Matcher pre-filter predicate (doc 14 §6.1) -----------------------------


def test_matcher_selects_in_scope_signal() -> None:
    assert signal_matches_icp(_signal(), _icp()) is True


def test_matcher_excludes_wrong_signal_type() -> None:
    assert signal_matches_icp(_signal(signal_type="news_mention"), _icp()) is False


def test_matcher_excludes_out_of_state() -> None:
    assert signal_matches_icp(_signal(state="CA"), _icp()) is False


def test_matcher_excludes_wrong_entity_kind() -> None:
    assert signal_matches_icp(_signal(entity_kind="city"), _icp()) is False


def test_matcher_excludes_out_of_size_band() -> None:
    assert signal_matches_icp(_signal(size=500), _icp()) is False
    assert signal_matches_icp(_signal(size=200000), _icp()) is False


def test_empty_icp_dimension_means_all_values() -> None:
    # Empty states array == "all states" (doc 14 §6.1): a CA signal still matches.
    assert signal_matches_icp(_signal(state="CA"), _icp(states=())) is True


def test_unknown_signal_dimension_only_matches_unrestricted_icp() -> None:
    # An unresolved entity (no state) fails a *restricting* states ICP...
    assert signal_matches_icp(_signal(state=None), _icp()) is False
    # ...but matches an ICP that does not restrict by state.
    assert signal_matches_icp(_signal(state=None), _icp(states=())) is True


def test_unknown_size_only_matches_open_band() -> None:
    assert signal_matches_icp(_signal(size=None), _icp()) is False
    assert signal_matches_icp(_signal(size=None), _icp(min_size=None, max_size=None)) is True


def test_size_band_boundaries_inclusive() -> None:
    assert signal_matches_icp(_signal(size=2000), _icp()) is True
    assert signal_matches_icp(_signal(size=100000), _icp()) is True


# --- Keyword gate (doc 14 §6.2, §3.1) ---------------------------------------


def test_keyword_excluded_is_hard_drop() -> None:
    with pytest.raises(KeywordExcludedError):
        keyword_match(
            "RFP for athletics equipment",
            required=("productivity",),
            excluded=("athletics",),
        )


def test_keyword_no_required_match_is_hard_drop() -> None:
    with pytest.raises(KeywordExcludedError):
        keyword_match("RFP for buses", required=("productivity software",), excluded=())


def test_keyword_partial_required_match_fraction() -> None:
    score, hits = keyword_match(
        "productivity software and collaboration",
        required=("productivity software", "document management"),
        excluded=(),
    )
    assert hits == ("productivity software",)
    assert score == pytest.approx(0.5)


def test_keyword_no_required_is_neutral() -> None:
    score, hits = keyword_match("anything", required=(), excluded=())
    assert score == 1.0
    assert hits == ()


# --- Recency decay (doc 14 §6.2) --------------------------------------------


def test_recency_fresh_is_one() -> None:
    assert recency_score(NOW, now=NOW, half_life_days=30.0) == 1.0


def test_recency_half_life() -> None:
    obs = NOW - timedelta(days=30)
    assert recency_score(obs, now=NOW, half_life_days=30.0) == pytest.approx(0.5, abs=1e-6)


def test_recency_missing_or_future_is_one() -> None:
    assert recency_score(None, now=NOW, half_life_days=30.0) == 1.0
    assert recency_score(NOW + timedelta(days=5), now=NOW, half_life_days=30.0) == 1.0


def test_recency_handles_naive_now() -> None:
    # A naive ``now`` against an aware ``observed_at`` must not raise TypeError —
    # both are normalised to UTC (Copilot review fix).
    naive_now = datetime(2026, 5, 22, 12, 0, 0)
    assert recency_score(NOW, now=naive_now, half_life_days=30.0) == 1.0


# --- Semantic similarity (doc 14 §6.2) --------------------------------------


def test_semantic_identical_vectors_is_one() -> None:
    assert semantic_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_semantic_orthogonal_is_neutral() -> None:
    assert semantic_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.5)


def test_semantic_missing_vector_is_none() -> None:
    assert semantic_similarity(None, [1.0]) is None
    assert semantic_similarity([1.0], None) is None
    assert semantic_similarity([1.0, 2.0], [1.0]) is None  # dim mismatch


# --- Full scorer + breakdown + threshold (doc 14 §6.2) ----------------------


def test_full_score_perfect_match_high_and_breakdown() -> None:
    result = score_signal_against_icp(
        _signal(),
        _icp(),
        text_blob="RFP: productivity software for the district",
        confidence=0.9,
        observed_at=NOW,
        now=NOW,
    )
    # All dimensions match, top signal-type weight, required keyword hit, fresh,
    # high confidence → a strong score, well above the 50 threshold.
    assert result.score >= 80.0
    assert result.passes_threshold is True

    # Breakdown carries each component's value/weight/points + matched flags +
    # bullets — exactly what F4's "Why this signal?" renders (doc 14 §6.2).
    components = result.breakdown["components"]
    assert set(components) >= {
        "signal_type_weight",
        "dimensions",
        "recency",
        "confidence",
        "keywords",
    }
    for comp in components.values():
        assert 0.0 <= comp["value"] <= 1.0
        assert comp["points"] >= 0.0
    matched = result.breakdown["matched"]
    assert matched["signal_type"] is True
    assert matched["state"] is True
    assert matched["states"] == ["WA"]
    assert matched["keywords"] == ["productivity software"]
    assert result.matched_keywords == ("productivity software",)
    assert any("matched state WA" in b for b in result.breakdown["bullets"])
    # No semantic component when no embeddings supplied (doc 14 §6.2 optional).
    assert "semantic" not in components


def test_full_score_semantic_component_included_when_vectors_present() -> None:
    result = score_signal_against_icp(
        _signal(),
        _icp(),
        text_blob="productivity software",
        confidence=0.9,
        observed_at=NOW,
        signal_vector=[1.0, 0.0],
        icp_vector=[1.0, 0.0],
        now=NOW,
    )
    assert "semantic" in result.breakdown["components"]
    assert result.breakdown["components"]["semantic"]["value"] == pytest.approx(1.0)


def test_full_score_excluded_keyword_raises() -> None:
    with pytest.raises(KeywordExcludedError):
        score_signal_against_icp(
            _signal(),
            _icp(),
            text_blob="RFP for athletics and productivity software",
            confidence=0.9,
            observed_at=NOW,
            now=NOW,
        )


def test_threshold_gate_below_does_not_pass() -> None:
    # A high threshold the (otherwise fine) signal cannot clear → no row earned.
    result = score_signal_against_icp(
        _signal(),
        _icp(threshold=99.5),
        text_blob="productivity software",
        confidence=0.5,
        observed_at=NOW - timedelta(days=120),
        now=NOW,
    )
    assert result.passes_threshold is False


def test_lower_signal_type_weight_lowers_score() -> None:
    hi = score_signal_against_icp(
        _signal(signal_type="rfp_posted"),
        _icp(),
        text_blob="productivity software",
        confidence=0.9,
        observed_at=NOW,
        now=NOW,
    )
    lo = score_signal_against_icp(
        _signal(signal_type="grant_awarded"),
        _icp(),
        text_blob="productivity software",
        confidence=0.9,
        observed_at=NOW,
        now=NOW,
    )
    # rfp_posted weight 1.0 vs grant_awarded 0.8 → the rfp scores higher, all else
    # equal (doc 14 §6.2 per-signal-type weight).
    assert hi.score > lo.score


def test_unrestricted_icp_dimensions_score_neutral_strength() -> None:
    # An ICP that restricts nothing (broad targeting) still scores an in-scope
    # signal — dimensions strength is a neutral 1.0, not 0.
    broad = IcpCriteria(
        signal_types=(),
        countries=(),
        states=(),
        entity_kinds=(),
        min_size=None,
        max_size=None,
        signal_weights={},
        keywords_required=(),
        keywords_excluded=(),
        threshold=50.0,
    )
    result = score_signal_against_icp(
        _signal(), broad, text_blob="anything", confidence=0.7, observed_at=NOW, now=NOW
    )
    assert result.breakdown["components"]["dimensions"]["value"] == 1.0
    assert result.passes_threshold is True


# --- Config validation ------------------------------------------------------


def test_component_weights_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        ComponentWeights(signal_type=0.9, dimensions=0.9)


def test_recency_half_life_must_be_positive() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        ScoringConfig(recency_half_life_days=0.0)
