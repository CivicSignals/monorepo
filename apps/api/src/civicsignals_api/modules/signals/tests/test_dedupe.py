"""Unit tests for the exact-match dedupe key/window logic (doc 19 §7; E5).

These exercise the pure key + window functions in ``signals.dedupe`` with no
database — the hash basis (entity_id + signal_type + normalized key fields),
per-type normalization (title casefold/whitespace, date-only reduction), the
per-type windows, and the in-memory merge. The windowed lookup against a live
``signals_signal`` table is in ``test_dedupe_db.py`` (gated on a Postgres DSN).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from civicsignals_api.modules.signals import dedupe
from civicsignals_api.modules.signals.models import Signal
from civicsignals_api.modules.signals.schemas import SignalType

# --- per-type windows (doc 19 §7.1) ---------------------------------------


def test_windows_match_doc_19_table() -> None:
    assert dedupe.window_for(SignalType.RFP_POSTED) == timedelta(days=90)
    assert dedupe.window_for(SignalType.CONTRACT_EXPIRING) == timedelta(days=365)
    assert dedupe.window_for(SignalType.BUDGET_APPROVED) == timedelta(days=365)
    assert dedupe.window_for(SignalType.LEADERSHIP_CHANGE) == timedelta(days=730)
    assert dedupe.window_for(SignalType.BOARD_AGENDA_ITEM) == timedelta(days=60)


def test_window_default_for_untabulated_type() -> None:
    # A type without an explicit window (news_mention) gets the sane default.
    assert dedupe.window_for(SignalType.NEWS_MENTION) == dedupe.DEFAULT_DEDUPE_WINDOW


# --- canonical hash basis (doc 19 §7.1) -----------------------------------


def test_same_key_hashes_identically() -> None:
    eid = uuid.uuid4()
    f = {"title": "RFP for ERP", "due_at": "2026-06-01T17:00:00Z"}
    h1 = dedupe.compute_dedupe_hash(SignalType.RFP_POSTED, eid, f)
    h2 = dedupe.compute_dedupe_hash(SignalType.RFP_POSTED, eid, dict(f))
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_title_case_and_whitespace_collapse() -> None:
    eid = uuid.uuid4()
    a = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, eid, {"title": "RFP for ERP", "due_at": "2026-06-01"}
    )
    b = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, eid, {"title": "  rfp   FOR  erp  ", "due_at": "2026-06-01"}
    )
    assert a == b


def test_due_at_date_only_ignores_time_of_day() -> None:
    eid = uuid.uuid4()
    a = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, eid, {"title": "RFP", "due_at": "2026-06-01T09:00:00Z"}
    )
    b = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, eid, {"title": "RFP", "due_at": "2026-06-01T23:59:00Z"}
    )
    assert a == b  # same calendar day -> same key (doc 19 §7.1 "date only")


def test_different_entity_does_not_collide() -> None:
    f = {"title": "RFP for ERP", "due_at": "2026-06-01"}
    a = dedupe.compute_dedupe_hash(SignalType.RFP_POSTED, uuid.uuid4(), f)
    b = dedupe.compute_dedupe_hash(SignalType.RFP_POSTED, uuid.uuid4(), f)
    assert a != b


def test_different_signal_type_does_not_collide() -> None:
    eid = uuid.uuid4()
    a = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, eid, {"title": "X", "due_at": "2026-06-01"}
    )
    b = dedupe.compute_dedupe_hash(SignalType.RFI_RFQ, eid, {"title": "X"})
    assert a != b


def test_none_entity_is_stable() -> None:
    f = {"title": "RFP", "due_at": "2026-06-01"}
    a = dedupe.compute_dedupe_hash(SignalType.RFP_POSTED, None, f)
    b = dedupe.compute_dedupe_hash(SignalType.RFP_POSTED, None, dict(f))
    assert a == b  # two resolution-pending sightings still dedupe (doc 19 §4.3)
    assert a != dedupe.compute_dedupe_hash(SignalType.RFP_POSTED, uuid.uuid4(), f)


def test_different_due_dates_distinct() -> None:
    eid = uuid.uuid4()
    a = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, eid, {"title": "RFP", "due_at": "2026-06-01"}
    )
    b = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, eid, {"title": "RFP", "due_at": "2026-07-01"}
    )
    assert a != b


# --- per-type key fields (doc 19 §7.1 table) -------------------------------


def test_budget_keys_on_fiscal_year_and_category() -> None:
    eid = uuid.uuid4()
    base = {"fiscal_year": "FY2026", "category": "cybersecurity", "amount_cents": 1}
    same_amount_differs = {
        "fiscal_year": "FY2026",
        "category": "cybersecurity",
        "amount_cents": 999,
    }
    # Amount is NOT a key field — same fiscal_year + category dedupes regardless.
    assert dedupe.compute_dedupe_hash(
        SignalType.BUDGET_APPROVED, eid, base
    ) == dedupe.compute_dedupe_hash(SignalType.BUDGET_APPROVED, eid, same_amount_differs)
    # Different category -> distinct.
    assert dedupe.compute_dedupe_hash(
        SignalType.BUDGET_APPROVED, eid, base
    ) != dedupe.compute_dedupe_hash(
        SignalType.BUDGET_APPROVED, eid, {"fiscal_year": "FY2026", "category": "ERP"}
    )


def test_leadership_keys_on_role_and_person() -> None:
    eid = uuid.uuid4()
    a = dedupe.compute_dedupe_hash(
        SignalType.LEADERSHIP_CHANGE, eid, {"role": "CTO", "person_name": "Jane Doe"}
    )
    b = dedupe.compute_dedupe_hash(
        SignalType.LEADERSHIP_CHANGE, eid, {"role": "cto", "person_name": "jane doe"}
    )
    assert a == b
    c = dedupe.compute_dedupe_hash(
        SignalType.LEADERSHIP_CHANGE, eid, {"role": "CIO", "person_name": "Jane Doe"}
    )
    assert a != c


def test_contract_expiring_keys_on_vendor_and_expiry() -> None:
    eid = uuid.uuid4()
    a = dedupe.compute_dedupe_hash(
        SignalType.CONTRACT_EXPIRING, eid, {"vendor_name": "Acme", "expires_at": "2026-12-31"}
    )
    b = dedupe.compute_dedupe_hash(
        SignalType.CONTRACT_EXPIRING, eid, {"vendor_name": "ACME", "expires_at": "2026-12-31"}
    )
    assert a == b
    assert a != dedupe.compute_dedupe_hash(
        SignalType.CONTRACT_EXPIRING, eid, {"vendor_name": "Acme", "expires_at": "2027-12-31"}
    )


# --- payload wrapper -------------------------------------------------------


def test_compute_for_payload_matches_raw_fields() -> None:
    from civicsignals_api.modules.signals.schemas import parse_signal_payload

    eid = uuid.uuid4()
    payload = parse_signal_payload(
        "rfp_posted",
        {"title": "RFP for ERP", "summary": "x", "due_at": "2026-06-01T17:00:00Z"},
    )
    via_payload = dedupe.compute_dedupe_hash_for_payload(payload, eid)
    # The validated payload serializes due_at to a normalized ISO string; the
    # date-only reduction makes it match a raw-fields hash for the same day.
    via_raw = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, eid, {"title": "RFP for ERP", "due_at": "2026-06-01"}
    )
    assert via_payload == via_raw


# --- in-memory merge (doc 19 §7.3) ----------------------------------------


def _signal(doc_ids: list[str], confidence: float | None) -> Signal:
    return Signal(
        id=uuid.uuid4(),
        signal_type="rfp_posted",
        recipe_id="r",
        raw_document_ids=list(doc_ids),
        content_hash="h",
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        title="t",
        summary="s",
        details={},
        confidence=confidence,
    )


def test_merge_appends_docs_preserving_all_sources() -> None:
    d1, d2, d3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    existing = _signal([str(d1)], 0.7)
    now = datetime(2026, 5, 1, tzinfo=UTC)
    dedupe.merge_signal(existing, new_doc_ids=[d2, d3], new_confidence=0.6, now=now)
    # All three source docs preserved, order kept, no loss (doc 19 §7.3).
    assert existing.raw_document_ids == [str(d1), str(d2), str(d3)]
    # Higher confidence kept (0.7 > 0.6).
    assert existing.confidence == 0.7
    # last-seen advanced.
    assert existing.observed_at == now


def test_merge_keeps_higher_confidence_and_dedups_docs() -> None:
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    existing = _signal([str(d1)], 0.5)
    dedupe.merge_signal(existing, new_doc_ids=[d1, d2], new_confidence=0.9, now=None)
    # d1 not duplicated; d2 appended.
    assert existing.raw_document_ids == [str(d1), str(d2)]
    assert existing.confidence == 0.9  # new higher value kept


def test_merge_handles_none_confidences() -> None:
    existing = _signal([], None)
    d1 = uuid.uuid4()
    dedupe.merge_signal(existing, new_doc_ids=[d1], new_confidence=None, now=None)
    assert existing.confidence is None
    assert existing.raw_document_ids == [str(d1)]
