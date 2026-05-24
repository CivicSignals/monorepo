"""Unit tests for the entities module that need no database (C1).

Covers UUID v7 generation, cursor round-tripping, fixture integrity, and the
kind-taxonomy invariants. The DB-backed schema/seed/service tests live in
``test_db.py`` and skip unless ``ENTITIES_TEST_DSN`` is set (same pattern as
``tests/test_seed_demo.py``).
"""

from __future__ import annotations

import csv
import io
import uuid
import zipfile
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


# --- Real NCES CCD format adaptation (doc 16 §4) ----------------------------
# The real CCD LEA universe download differs from the tiny sample fixture: it
# ships as a ZIP of a Latin-1 CSV with ~58 columns, uses ``ST`` (not STABBR) for
# the state, carries free-text status, and omits enrollment/county. These cover
# the parsing seam without a DB (the DB-backed load is in test_db.py).

REAL_FORMAT_FIXTURE = "nces_ccd_real_format_sample.csv"


def test_real_format_fixture_has_real_ccd_columns() -> None:
    path = seeding.FIXTURES_DIR / REAL_FORMAT_FIXTURE
    assert path.exists(), f"missing real-format fixture {REAL_FORMAT_FIXTURE}"
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))
    assert rows
    header = set(rows[0].keys())
    # The real CCD directory header — NOT the sample's STABBR/TOTAL_STUDENTS.
    assert {"ST", "LEAID", "FIPST", "LEA_NAME", "WEBSITE", "UPDATED_STATUS_TEXT"} <= header
    assert "STABBR" not in header and "TOTAL_STUDENTS" not in header
    leaids = [r["LEAID"].strip() for r in rows]
    assert all(leaids) and len(leaids) == len(set(leaids))


def test_read_rows_unzips_and_decodes_latin1() -> None:
    """The read_rows seam transparently unzips and decodes Latin-1, like the real file."""
    # A CSV with a Latin-1-only byte (é = 0xE9) that is NOT valid UTF-8.
    csv_text = "LEAID,LEA_NAME,ST\n9900001,C\xe9sar Ch\xe1vez School District,CA\n"
    csv_bytes = csv_text.encode("latin-1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ccd_lea_029_test.csv", csv_bytes)
        zf.writestr("ccd_lea_029_test.sas7bdat", b"\x00\x01ignored-binary")
    zip_path = Path(_write_tmp("ccd_test.zip", buf.getvalue()))
    rows = list(seeding.read_rows(zip_path))
    assert len(rows) == 1
    assert rows[0]["LEAID"] == "9900001"
    # The Latin-1 accented chars decoded correctly (not mojibake / not raising).
    assert "C\xe9sar Ch\xe1vez" in rows[0]["LEA_NAME"]


def test_read_rows_plain_csv_still_works() -> None:
    """The sample-fixture path (plain UTF-8 CSV) is unchanged by the zip/encoding seam."""
    rows = list(seeding.read_rows(seeding.FIXTURES_DIR / "nces_ccd_sample.csv"))
    assert rows and rows[0]["LEAID"] == "5304350"


def test_nces_status_maps_free_text() -> None:
    assert seeding._nces_status({"UPDATED_STATUS_TEXT": "Open"}) == "active"
    assert seeding._nces_status({"SY_STATUS_TEXT": "Open"}) == "active"
    assert seeding._nces_status({"UPDATED_STATUS_TEXT": "Closed"}) == "dissolved"
    # Future/added-but-not-operational stays in the directory as active.
    assert seeding._nces_status({"UPDATED_STATUS_TEXT": "Added but not yet operational"}) == "active"
    assert seeding._nces_status({}) == "active"
    # UPDATED_STATUS_TEXT takes precedence over SY_STATUS_TEXT.
    assert (
        seeding._nces_status({"UPDATED_STATUS_TEXT": "Closed", "SY_STATUS_TEXT": "Open"})
        == "dissolved"
    )


def test_state_fips_covers_all_states_and_abbr_reverse() -> None:
    # Full national coverage so a real bulk load parents every state's rows.
    assert len(seeding.STATE_FIPS) >= 51  # 50 states + DC (+ territories)
    assert seeding.STATE_FIPS["06"] == ("CA", "California")
    assert seeding.STATE_FIPS["53"] == ("WA", "Washington")
    # Reverse lookup is consistent both ways.
    for fips, (abbr, _name) in seeding.STATE_FIPS.items():
        assert seeding._ABBR_TO_FIPS[abbr] == fips


def _write_tmp(name: str, data: bytes) -> str:
    import tempfile

    path = Path(tempfile.gettempdir()) / f"civicsignals_entities_test_{name}"
    path.write_bytes(data)
    return str(path)
