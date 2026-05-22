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
    MAX_FEEDBACK_ADJUSTMENT,
    MIN_FEEDBACK_FOR_OVERRIDE,
    ComponentWeights,
    IcpCriteria,
    KeywordExcludedError,
    ScoringConfig,
    SignalDimensions,
    derive_signal_type_overrides,
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


# --- F5 feedback re-weighting (pure override-derivation math, doc 14 §12) ----


def test_no_feedback_yields_empty_override_map() -> None:
    # The no-op default: no feedback → no nudge → empty map.
    assert derive_signal_type_overrides({}) == {}


def test_below_volume_floor_is_ignored() -> None:
    # Fewer than MIN_FEEDBACK_FOR_OVERRIDE data points → no nudge (one click can't move).
    counts = {"rfp_posted": (MIN_FEEDBACK_FOR_OVERRIDE - 1, 0)}
    assert derive_signal_type_overrides(counts) == {}


def test_all_relevant_nudges_weight_up_to_the_bound() -> None:
    # Unanimous relevant (net = +1) → max upward nudge.
    counts = {"rfp_posted": (5, 0)}
    overrides = derive_signal_type_overrides(counts)
    assert overrides["rfp_posted"] == pytest.approx(1.0 + MAX_FEEDBACK_ADJUSTMENT)


def test_all_not_relevant_nudges_weight_down_to_the_bound() -> None:
    # Unanimous not_relevant (net = -1) → max downward nudge.
    counts = {"news_mention": (0, 7)}
    overrides = derive_signal_type_overrides(counts)
    assert overrides["news_mention"] == pytest.approx(1.0 - MAX_FEEDBACK_ADJUSTMENT)


def test_partial_sentiment_scales_proportionally() -> None:
    # net = (3 - 1) / 4 = 0.5 → 1 + 0.25*0.5 = 1.125.
    counts = {"rfp_posted": (3, 1)}
    overrides = derive_signal_type_overrides(counts)
    assert overrides["rfp_posted"] == pytest.approx(1.0 + MAX_FEEDBACK_ADJUSTMENT * 0.5)


def test_balanced_feedback_is_a_noop() -> None:
    # net = 0 → multiplier exactly 1.0 → omitted (kept sparse, no nudge).
    counts = {"rfp_posted": (3, 3)}
    assert derive_signal_type_overrides(counts) == {}


def test_override_is_always_within_the_clamp_band() -> None:
    # Even with a huge max_adjustment passed, the result clamps to [lo, hi].
    counts = {"rfp_posted": (100, 0), "news_mention": (0, 100)}
    overrides = derive_signal_type_overrides(counts, max_adjustment=5.0)
    assert overrides["rfp_posted"] == pytest.approx(6.0)  # 1 + 5*1
    assert overrides["news_mention"] == pytest.approx(-4.0)  # 1 - 5*1 (clamp = lo)


def test_wrong_extraction_is_not_a_scoring_input() -> None:
    # derive_signal_type_overrides only knows (relevant, not_relevant) — wrong_extraction
    # is filtered upstream and never reaches here. A type with only wrong_extraction
    # feedback (modelled as (0, 0)) produces no override.
    counts = {"rfp_posted": (0, 0)}
    assert derive_signal_type_overrides(counts) == {}


def test_overrides_apply_multiplicatively_to_the_signal_type_component() -> None:
    # A downward nudge on the signal type drops the blended score vs. the baseline,
    # while a config with no overrides scores identically to the default config.
    icp = _icp(signal_weights={"rfp_posted": 1.0})
    kwargs: dict[str, object] = {
        "text_blob": "RFP for productivity software",
        "confidence": 0.9,
        "observed_at": NOW,
        "now": NOW,
    }
    baseline = score_signal_against_icp(_signal(), icp, **kwargs)  # type: ignore[arg-type]
    nudged_down = score_signal_against_icp(
        _signal(),
        icp,
        config=ScoringConfig(signal_type_weight_overrides={"rfp_posted": 0.75}),
        **kwargs,  # type: ignore[arg-type]
    )
    nudged_up_clamped = score_signal_against_icp(
        _signal(),
        icp,
        config=ScoringConfig(signal_type_weight_overrides={"rfp_posted": 1.25}),
        **kwargs,  # type: ignore[arg-type]
    )
    # A no-feedback config is a byte-for-byte no-op vs. the default.
    no_feedback = score_signal_against_icp(_signal(), icp, config=ScoringConfig(), **kwargs)  # type: ignore[arg-type]
    assert no_feedback.score == baseline.score
    assert nudged_down.score < baseline.score
    # base weight 1.0 is already clamped at 1.0, so an upward nudge cannot exceed it.
    assert nudged_up_clamped.score == baseline.score


def test_unweighted_type_can_be_nudged_up() -> None:
    # When the ICP pinned no weight (falls back to DEFAULT_SIGNAL_TYPE_WEIGHT=0.6), an
    # upward nudge has headroom and raises the score.
    icp = _icp(signal_weights={})  # rfp_posted not pinned → default 0.6
    kwargs: dict[str, object] = {
        "text_blob": "RFP for productivity software",
        "confidence": 0.9,
        "observed_at": NOW,
        "now": NOW,
    }
    baseline = score_signal_against_icp(_signal(), icp, **kwargs)  # type: ignore[arg-type]
    nudged_up = score_signal_against_icp(
        _signal(),
        icp,
        config=ScoringConfig(signal_type_weight_overrides={"rfp_posted": 1.25}),
        **kwargs,  # type: ignore[arg-type]
    )
    assert nudged_up.score > baseline.score


def test_scoring_config_rejects_out_of_band_override() -> None:
    with pytest.raises(ValueError, match="feedback band"):
        ScoringConfig(signal_type_weight_overrides={"rfp_posted": 2.0})
