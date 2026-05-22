"""Pure (no-DB) unit tests for the centralized H2 filter-validation rule engine.

One test per invalid-combination rule, asserting both that it fires *and* that
the surfaced message is explicit (not a generic "invalid") — the H2 acceptance
criterion. The rule engine is pure, so these need no database or app.
"""

from __future__ import annotations

from datetime import UTC, datetime

from civicsignals_api.modules.searches.validation import (
    FilterValidationError,
    assert_valid_filters,
    validate_filters,
)


def _codes(raw: dict[str, object]) -> set[str]:
    return {v.code for v in validate_filters(raw)}


# --- The valid baselines -----------------------------------------------------


def test_empty_filters_have_no_violations() -> None:
    assert validate_filters({}) == []


def test_fully_specified_valid_filters_pass() -> None:
    assert (
        validate_filters(
            {
                "signal_type": "rfp_posted",
                "statuses": ["new", "pinned"],
                "min_score": 60.0,
                "published_at_gte": "2026-01-01T00:00:00+00:00",
                "published_at_lt": "2026-02-01T00:00:00+00:00",
            }
        )
        == []
    )


# --- Each invalid-combination rule -------------------------------------------


def test_unknown_filter_key_is_rejected_with_explicit_message() -> None:
    violations = validate_filters({"entity_id": "abc"})
    assert [v.code for v in violations] == ["unknown_filter"]
    assert "not a filter you can save" in violations[0].message
    assert violations[0].field == "entity_id"


def test_unknown_signal_type_is_rejected_with_explicit_message() -> None:
    violations = validate_filters({"signal_type": "not_a_type"})
    assert [v.code for v in violations] == ["unknown_signal_type"]
    assert "not a known signal type" in violations[0].message


def test_empty_statuses_list_is_rejected() -> None:
    violations = validate_filters({"statuses": []})
    assert [v.code for v in violations] == ["statuses_empty"]
    assert "matches nothing" in violations[0].message


def test_unknown_status_value_is_rejected() -> None:
    violations = validate_filters({"statuses": ["new", "bogus"]})
    assert [v.code for v in violations] == ["unknown_status"]
    assert "bogus" in violations[0].message


def test_statuses_wrong_type_is_rejected() -> None:
    assert _codes({"statuses": "new"}) == {"statuses_not_a_list"}


def test_min_score_above_range_is_rejected_with_value_in_message() -> None:
    violations = validate_filters({"min_score": 150})
    assert [v.code for v in violations] == ["min_score_out_of_range"]
    assert "150" in violations[0].message


def test_min_score_below_range_is_rejected() -> None:
    assert _codes({"min_score": -1}) == {"min_score_out_of_range"}


def test_min_score_non_numeric_is_rejected() -> None:
    assert _codes({"min_score": "high"}) == {"min_score_not_a_number"}


def test_min_score_bool_is_rejected_as_non_numeric() -> None:
    # ``True`` is an ``int`` subclass; the rule must not silently accept it as 1.
    assert _codes({"min_score": True}) == {"min_score_not_a_number"}


def test_invalid_date_string_is_rejected() -> None:
    assert _codes({"published_at_gte": "not-a-date"}) == {"invalid_date"}


def test_inverted_date_range_is_rejected() -> None:
    violations = validate_filters(
        {
            "published_at_gte": "2026-02-01T00:00:00+00:00",
            "published_at_lt": "2026-01-01T00:00:00+00:00",
        }
    )
    assert [v.code for v in violations] == ["date_range_inverted"]
    assert "before the end date" in violations[0].message


def test_equal_date_range_is_rejected_as_empty_window() -> None:
    moment = "2026-01-01T00:00:00+00:00"
    assert _codes({"published_at_gte": moment, "published_at_lt": moment}) == {
        "date_range_inverted"
    }


def test_date_range_accepts_native_datetimes() -> None:
    assert (
        validate_filters(
            {
                "published_at_gte": datetime(2026, 1, 1, tzinfo=UTC),
                "published_at_lt": datetime(2026, 2, 1, tzinfo=UTC),
            }
        )
        == []
    )


# --- Multiple violations are reported at once (one message per broken rule) --


def test_multiple_violations_are_all_reported() -> None:
    violations = validate_filters(
        {
            "signal_type": "nope",
            "min_score": 999,
            "statuses": [],
        }
    )
    codes = {v.code for v in violations}
    assert codes == {"unknown_signal_type", "min_score_out_of_range", "statuses_empty"}
    # Every violation carries a distinct, non-empty, human-readable message.
    assert all(v.message and v.message != "invalid" for v in violations)


# --- The raising helper / RFC 7807 rendering --------------------------------


def test_assert_valid_filters_is_silent_for_valid_input() -> None:
    assert_valid_filters({"signal_type": "rfp_posted"})


def test_assert_valid_filters_raises_with_all_violations() -> None:
    try:
        assert_valid_filters({"signal_type": "nope", "min_score": 200})
    except FilterValidationError as exc:
        assert len(exc.violations) == 2
        # The errors[] rendering carries field/code/message for the API.
        rendered = [v.as_error() for v in exc.violations]
        assert all({"field", "code", "message"} <= set(e) for e in rendered)
    else:  # pragma: no cover
        raise AssertionError("expected FilterValidationError")
