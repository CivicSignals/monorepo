"""Pure (no-DB) tests for the searches schema layer (H1 filter validation).

These exercise :class:`SearchFilters` / :class:`SavedSearchCreate` directly so
the filter-validation contract is covered without a database — the DB-backed
flow tests (``test_searches_flow``) cover CRUD / isolation / sharing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from civicsignals_api.modules.searches.schemas import (
    SavedSearchCreate,
    SearchFilters,
)


def test_empty_filters_serialize_to_empty_blob() -> None:
    assert SearchFilters().to_storage() == {}


def test_full_filters_round_trip_to_json_native() -> None:
    filters = SearchFilters(
        signal_type="rfp_posted",
        statuses=["new", "pinned"],
        min_score=42.5,
        published_at_gte=datetime(2026, 1, 1, tzinfo=UTC),
        published_at_lt=datetime(2026, 2, 1, tzinfo=UTC),
    )
    blob = filters.to_storage()
    assert blob["signal_type"] == "rfp_posted"
    assert blob["statuses"] == ["new", "pinned"]
    assert blob["min_score"] == 42.5
    # Datetimes serialize to ISO strings so the JSONB column round-trips.
    assert isinstance(blob["published_at_gte"], str)
    assert isinstance(blob["published_at_lt"], str)


def test_none_filters_are_dropped_from_storage() -> None:
    blob = SearchFilters(signal_type="news_mention").to_storage()
    assert blob == {"signal_type": "news_mention"}


def test_unknown_signal_type_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(signal_type="not_a_signal_type")


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(statuses=["bogus_status"])


def test_known_statuses_are_accepted_and_deduplicated() -> None:
    filters = SearchFilters(statuses=["new", "new", "reviewed"])
    assert filters.statuses == ["new", "reviewed"]


def test_min_score_out_of_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(min_score=150.0)
    with pytest.raises(ValidationError):
        SearchFilters(min_score=-1.0)


def test_inverted_date_range_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchFilters(
            published_at_gte=datetime(2026, 2, 1, tzinfo=UTC),
            published_at_lt=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_equal_date_range_is_rejected() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        SearchFilters(published_at_gte=moment, published_at_lt=moment)


def test_unknown_filter_key_is_forbidden() -> None:
    # The H2 rule engine rejects a stray filter key with an explicit message;
    # constructed directly, that surfaces as a Pydantic ValidationError.
    with pytest.raises(ValidationError):
        SearchFilters.model_validate({"entity_id": "abc"})


def test_create_defaults_to_empty_private_search() -> None:
    body = SavedSearchCreate(name="My feed")
    assert body.is_shared is False
    # ``filters`` is now the raw mapping; ``validated_filters`` parses it (H2).
    assert body.filters == {}
    assert body.validated_filters().to_storage() == {}


def test_create_requires_non_empty_name() -> None:
    with pytest.raises(ValidationError):
        SavedSearchCreate(name="")


def test_create_rejects_unknown_top_level_key() -> None:
    with pytest.raises(ValidationError):
        SavedSearchCreate(name="ok", workspace_id="sneaky")  # type: ignore[call-arg]
