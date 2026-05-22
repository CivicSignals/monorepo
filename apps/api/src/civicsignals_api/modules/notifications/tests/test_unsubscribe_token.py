"""Unit tests for the H5 signed one-click unsubscribe token (pure — no DB/SMTP).

Cover the round-trip (a freshly minted token verifies to the same subscription id),
plus the three tamper-resistance properties the security note promises: a tampered
token is rejected, an expired token is rejected, and a token minted for one
subscription cannot identify a different one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from civicsignals_api.config import Settings
from civicsignals_api.modules.notifications.unsubscribe import (
    InvalidUnsubscribeToken,
    make_unsubscribe_token,
    verify_unsubscribe_token,
)

_SETTINGS = Settings(secret_key="unit-test-secret")


def test_round_trip_returns_same_subscription_id() -> None:
    sub_id = uuid.uuid4()
    token = make_unsubscribe_token(sub_id, settings=_SETTINGS)
    assert verify_unsubscribe_token(token, settings=_SETTINGS) == sub_id


def test_tampered_signature_is_rejected() -> None:
    token = make_unsubscribe_token(uuid.uuid4(), settings=_SETTINGS)
    version, payload, sig = token.split(".")
    # Flip the last hex char of the signature.
    bad_char = "0" if sig[-1] != "0" else "1"
    tampered = f"{version}.{payload}.{sig[:-1]}{bad_char}"
    with pytest.raises(InvalidUnsubscribeToken):
        verify_unsubscribe_token(tampered, settings=_SETTINGS)


def test_tampered_payload_is_rejected() -> None:
    """Editing the payload (e.g. to point at another subscription) breaks the sig."""
    token = make_unsubscribe_token(uuid.uuid4(), settings=_SETTINGS)
    other = make_unsubscribe_token(uuid.uuid4(), settings=_SETTINGS)
    _, _, sig = token.split(".")
    version, other_payload, _ = other.split(".")
    # Graft another token's payload onto this token's signature.
    spliced = f"{version}.{other_payload}.{sig}"
    with pytest.raises(InvalidUnsubscribeToken):
        verify_unsubscribe_token(spliced, settings=_SETTINGS)


def test_expired_token_is_rejected() -> None:
    sub_id = uuid.uuid4()
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    token = make_unsubscribe_token(sub_id, settings=_SETTINGS, now=issued)
    # Verify well past the TTL.
    later = issued + timedelta(seconds=_SETTINGS.digest_unsubscribe_ttl_seconds + 1)
    with pytest.raises(InvalidUnsubscribeToken):
        verify_unsubscribe_token(token, settings=_SETTINGS, now=later)
    # …but still valid just before expiry.
    before = issued + timedelta(seconds=_SETTINGS.digest_unsubscribe_ttl_seconds - 1)
    assert verify_unsubscribe_token(token, settings=_SETTINGS, now=before) == sub_id


def test_wrong_secret_is_rejected() -> None:
    """A token signed with another secret (key rotation) does not verify."""
    token = make_unsubscribe_token(uuid.uuid4(), settings=_SETTINGS)
    other = Settings(secret_key="a-different-secret")
    with pytest.raises(InvalidUnsubscribeToken):
        verify_unsubscribe_token(token, settings=other)


def test_malformed_tokens_are_rejected() -> None:
    for bad in ["", "not-a-token", "v1.only-two", "v2.payload.sig"]:
        with pytest.raises(InvalidUnsubscribeToken):
            verify_unsubscribe_token(bad, settings=_SETTINGS)


def test_token_for_one_subscription_does_not_unsubscribe_another() -> None:
    """The id is bound by the signature: A's token verifies to A, never B."""
    a = uuid.uuid4()
    b = uuid.uuid4()
    token_a = make_unsubscribe_token(a, settings=_SETTINGS)
    resolved = verify_unsubscribe_token(token_a, settings=_SETTINGS)
    assert resolved == a
    assert resolved != b
