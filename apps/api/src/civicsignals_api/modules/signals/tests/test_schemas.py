"""Strict per-signal-type output schema tests (E4; doc 19 §5.1, §6.1).

These are pure validation tests — no DB, no I/O — covering:

- the hard gate: a valid candidate parses into its typed payload; a candidate
  missing a required field, of an unknown type, or with an extra/unknown field is
  rejected with a :class:`SignalValidationError` that surfaces the error;
- every MVP signal type has a schema with the documented required fields.
"""

from __future__ import annotations

import pytest

from civicsignals_api.modules.signals.schemas import (
    PAYLOAD_BY_TYPE,
    BudgetApprovedPayload,
    ContractExpiringPayload,
    LeadershipChangePayload,
    RFPPostedPayload,
    SignalType,
    SignalValidationError,
    parse_signal_payload,
)

_COMMON = {"title": "A title", "summary": "A one-line summary."}


def test_every_mvp_signal_type_has_a_schema() -> None:
    """The dispatch table covers exactly the 12 MVP types (doc 19 §5.1)."""
    assert set(PAYLOAD_BY_TYPE) == set(SignalType)
    assert len(PAYLOAD_BY_TYPE) == 12


def test_rfp_posted_valid_parses() -> None:
    payload = parse_signal_payload(
        "rfp_posted",
        {**_COMMON, "due_at": "2026-06-01T17:00:00Z", "amount_cents": 40000000},
    )
    assert isinstance(payload, RFPPostedPayload)
    assert payload.signal_type is SignalType.RFP_POSTED
    assert payload.amount_cents == 40000000
    # The dumped payload (the signals_signal.details_jsonb) round-trips the type.
    assert payload.model_dump(mode="json")["signal_type"] == "rfp_posted"


def test_rfp_posted_missing_required_due_at_rejected() -> None:
    """Missing a type-required field → rejected with the field surfaced (doc 19 §6.1)."""
    with pytest.raises(SignalValidationError) as exc:
        parse_signal_payload("rfp_posted", {**_COMMON})  # no due_at
    assert exc.value.signal_type == "rfp_posted"
    assert any("due_at" in e for e in exc.value.errors)


def test_missing_title_rejected_for_any_type() -> None:
    """title + summary are required for EVERY type (doc 14 §6.2 keyword scoring)."""
    with pytest.raises(SignalValidationError) as exc:
        parse_signal_payload("news_mention", {"summary": "only a summary"})
    assert any("title" in e for e in exc.value.errors)


def test_empty_summary_rejected() -> None:
    with pytest.raises(SignalValidationError):
        parse_signal_payload("news_mention", {"title": "t", "summary": ""})


def test_unknown_signal_type_rejected() -> None:
    with pytest.raises(SignalValidationError) as exc:
        parse_signal_payload("not_a_real_type", {**_COMMON})
    assert "unknown signal type" in exc.value.errors[0]


def test_none_signal_type_rejected() -> None:
    with pytest.raises(SignalValidationError) as exc:
        parse_signal_payload(None, {**_COMMON})
    assert exc.value.signal_type is None
    assert "no signal_type" in exc.value.errors[0]


def test_extra_field_rejected() -> None:
    """extra='forbid' makes a drifting prompt's unknown key a hard error (§13.2)."""
    with pytest.raises(SignalValidationError) as exc:
        parse_signal_payload("news_mention", {**_COMMON, "totally_unexpected_field": "x"})
    assert any("totally_unexpected_field" in e for e in exc.value.errors)


def test_negative_amount_rejected() -> None:
    with pytest.raises(SignalValidationError):
        parse_signal_payload(
            "budget_approved",
            {**_COMMON, "amount_cents": -5, "category": "IT", "fiscal_year": "2026"},
        )


def test_contract_expiring_required_fields() -> None:
    payload = parse_signal_payload(
        "contract_expiring",
        {**_COMMON, "vendor_name": "Acme", "expires_at": "2026-12-31T00:00:00Z"},
    )
    assert isinstance(payload, ContractExpiringPayload)
    assert payload.vendor_name == "Acme"
    # Missing vendor_name → rejected.
    with pytest.raises(SignalValidationError):
        parse_signal_payload("contract_expiring", {**_COMMON, "expires_at": "2026-12-31T00:00:00Z"})


def test_budget_approved_required_fields() -> None:
    payload = parse_signal_payload(
        "budget_approved",
        {**_COMMON, "amount_cents": 1000000, "category": "technology", "fiscal_year": "2026"},
    )
    assert isinstance(payload, BudgetApprovedPayload)
    assert payload.fiscal_year == "2026"


def test_leadership_change_required_fields() -> None:
    payload = parse_signal_payload(
        "leadership_change",
        {**_COMMON, "role": "CTO", "person_name": "Jordan Lee"},
    )
    assert isinstance(payload, LeadershipChangePayload)
    assert payload.role == "CTO"
    with pytest.raises(SignalValidationError):
        parse_signal_payload("leadership_change", {**_COMMON, "role": "CTO"})  # no person


@pytest.mark.parametrize("signal_type", [t.value for t in SignalType])
def test_every_type_parses_with_its_minimal_required_payload(signal_type: str) -> None:
    """Each type validates given its documented minimal required fields."""
    minimal: dict[str, object] = {**_COMMON}
    # Type-specific required fields (doc 19 §6.1).
    required: dict[str, dict[str, object]] = {
        "rfp_posted": {"due_at": "2026-06-01T17:00:00Z"},
        "contract_expiring": {"vendor_name": "V", "expires_at": "2026-12-31T00:00:00Z"},
        "contract_awarded": {"vendor_name": "V"},
        "budget_approved": {"amount_cents": 1, "category": "c", "fiscal_year": "2026"},
        "leadership_change": {"role": "CTO", "person_name": "P"},
        "board_agenda_item": {"meeting_date": "2026-06-01T18:00:00Z", "topic": "RFP"},
        "open_job": {"role": "Buyer"},
    }
    minimal.update(required.get(signal_type, {}))
    payload = parse_signal_payload(signal_type, minimal)
    assert payload.signal_type.value == signal_type
