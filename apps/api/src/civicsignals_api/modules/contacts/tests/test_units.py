"""Unit tests for the contacts module that need no database (C2).

Covers cursor round-tripping, model constant invariants, and ProvenanceInput/
ContactInput dataclass defaults. DB-backed tests live in ``test_db.py`` and skip
unless a DSN is configured.
"""

from __future__ import annotations

import uuid

import pytest

from civicsignals_api.modules.contacts import services
from civicsignals_api.modules.contacts.ids import uuid7
from civicsignals_api.modules.contacts.models import CONTACT_STATUSES, EMAIL_STATUSES

# ---------------------------------------------------------------------------
# UUID v7 (same checks as entities — contacts/ids.py is a verbatim copy).
# ---------------------------------------------------------------------------


def _uuid7_timestamp_ms(value: uuid.UUID) -> int:
    """Extract the 48-bit Unix-ms timestamp prefix of a v7 UUID (RFC 9562)."""
    return value.int >> 80


def test_uuid7_is_version_7() -> None:
    value = uuid7()
    assert value.version == 7
    # RFC 4122 / 9562 variant bits = 0b10.
    assert (value.int >> 62) & 0b11 == 0b10


def test_uuid7_is_time_ordered() -> None:
    import time

    before = time.time_ns() // 1_000_000
    samples = [uuid7() for _ in range(50)]
    after = time.time_ns() // 1_000_000
    timestamps = [_uuid7_timestamp_ms(s) for s in samples]
    assert timestamps == sorted(timestamps)
    assert before <= timestamps[0]
    assert timestamps[-1] <= after


def test_uuid7_unique() -> None:
    ids = {uuid7() for _ in range(500)}
    assert len(ids) == 500


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------


def test_cursor_round_trips() -> None:
    cid = uuid7()
    assert services.decode_cursor(services.encode_cursor(cid)) == cid


def test_decode_cursor_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        services.decode_cursor("not-a-valid-cursor!!")


# ---------------------------------------------------------------------------
# Model constant invariants
# ---------------------------------------------------------------------------


def test_contact_statuses_non_empty() -> None:
    assert CONTACT_STATUSES
    assert "active" in CONTACT_STATUSES
    assert "stale" in CONTACT_STATUSES


def test_email_statuses_match_spec() -> None:
    # doc 07 §4: unverified → valid | risky | invalid → stale
    assert set(EMAIL_STATUSES) == {"unverified", "valid", "risky", "invalid", "stale"}


# ---------------------------------------------------------------------------
# ContactInput / ProvenanceInput dataclass defaults
# ---------------------------------------------------------------------------


def test_contact_input_defaults() -> None:
    entity_id = uuid.uuid4()
    inp = services.ContactInput(entity_id=entity_id, name="Alice Smith")
    assert inp.status == "active"
    assert inp.canonical_email is None
    assert inp.emails == []
    assert inp.phones == []
    assert inp.titles == []
    assert inp.attributes == {}
    assert inp.provenance.verified is False
    assert inp.provenance.source is None


def test_provenance_input_defaults() -> None:
    prov = services.ProvenanceInput()
    assert prov.source is None
    assert prov.source_url is None
    assert prov.confidence is None
    assert prov.verified is False
    assert prov.last_verified_at is None


def test_email_input_defaults() -> None:
    ei = services.EmailInput(email="alice@example.gov")
    assert ei.is_primary is False
    assert ei.email_status == "unverified"


def test_phone_input_defaults() -> None:
    pi = services.PhoneInput(phone="+12065550100")
    assert pi.phone_type is None
    assert pi.is_primary is False


def test_title_input_defaults() -> None:
    ti = services.TitleInput(title="Director of Curriculum")
    assert ti.is_current is True
    assert ti.department is None
    assert ti.first_observed_at is None
