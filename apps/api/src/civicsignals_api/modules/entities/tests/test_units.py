"""Unit tests for the entities module that need no database (C1).

Covers UUID v7 generation, cursor round-tripping, fixture integrity, and the
kind-taxonomy invariants. The DB-backed schema/seed/service tests live in
``test_db.py`` and skip unless ``ENTITIES_TEST_DSN`` is set (same pattern as
``tests/test_seed_demo.py``).
"""

from __future__ import annotations

import csv
import uuid
from pathlib import Path

import pytest

from civicsignals_api.modules.entities import seeding, services
from civicsignals_api.modules.entities.ids import uuid7
from civicsignals_api.modules.entities.models import ENTITY_TYPES


def _uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Extract the 48-bit Unix-ms timestamp prefix of a v7 UUID (RFC 9562)."""
    return value.int >> 80


def test_uuid7_is_version_and_variant() -> None:
    value = uuid7()
    assert isinstance(value, uuid.UUID)
    assert value.version == 7
    # RFC 4122 / 9562 variant bits = 0b10.
    assert (value.int >> 62) & 0b11 == 0b10


def test_uuid7_is_time_ordered() -> None:
    import time

    before = time.time_ns() // 1_000_000
    samples = [uuid7() for _ in range(50)]
    after = time.time_ns() // 1_000_000
    timestamps = [_uuid7_timestamp_ms(s) for s in samples]
    # Timestamps are non-decreasing and bracketed by wall-clock either side.
    assert timestamps == sorted(timestamps)
    assert before <= timestamps[0]
    assert timestamps[-1] <= after


def test_uuid7_unique() -> None:
    ids = {uuid7() for _ in range(1000)}
    assert len(ids) == 1000


def test_cursor_round_trips() -> None:
    entity_id = uuid7()
    assert services.decode_cursor(services.encode_cursor(entity_id)) == entity_id


def test_decode_cursor_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        services.decode_cursor("not-a-cursor!!")


def test_kind_seed_slugs_are_valid_types() -> None:
    seed_slugs = [row[0] for row in seeding.KIND_SEED]
    # Every seeded kind slug must be a canonical entity type.
    assert set(seed_slugs) <= set(ENTITY_TYPES)
    # And every canonical type must have a taxonomy row.
    assert set(seed_slugs) == set(ENTITY_TYPES)
    # No duplicate slugs / categories well-formed.
    assert len(seed_slugs) == len(set(seed_slugs))


def test_kind_seed_categories_known() -> None:
    categories = {row[2] for row in seeding.KIND_SEED}
    assert categories <= {"k12", "higher_ed", "local_gov", "state_gov", "special"}


@pytest.mark.parametrize(
    ("fixture", "key_col"),
    [
        ("nces_ccd_sample.csv", "LEAID"),
        ("ipeds_hd_sample.csv", "UNITID"),
        ("census_gov_sample.csv", "GID"),
    ],
)
def test_fixture_natural_keys_unique_and_present(fixture: str, key_col: str) -> None:
    path = seeding.FIXTURES_DIR / fixture
    assert path.exists(), f"missing sample fixture {fixture}"
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert rows, f"{fixture} has no rows"
    keys = [r[key_col].strip() for r in rows]
    assert all(keys), f"{fixture} has a blank {key_col}"
    assert len(keys) == len(set(keys)), f"{fixture} has duplicate {key_col}"


def test_fixtures_dir_is_packaged() -> None:
    # Sanity: fixtures live under the module so they ship with the wheel.
    assert Path(seeding.__file__).parent / "fixtures" == seeding.FIXTURES_DIR


def test_to_int_helper() -> None:
    assert seeding._to_int("23,400") == 23400
    assert seeding._to_int("") is None
    assert seeding._to_int(None) is None
    assert seeding._to_int("abc") is None
    assert seeding._to_int("52000") == 52000


def test_clean_url_helper() -> None:
    assert seeding._clean_url("www.example.org") == "https://www.example.org"
    assert seeding._clean_url("https://x.gov") == "https://x.gov"
    assert seeding._clean_url("") is None
    assert seeding._clean_url(None) is None
