"""Signed one-click digest-unsubscribe tokens (H5).

A digest email carries a stateless, tamper-proof token that lets the recipient
turn *that one* subscription off without logging in — both via a footer link and
via the RFC 8058 ``List-Unsubscribe`` mail-client button. Mirrors the existing
HMAC self-verifying token patterns (the B3 reset is a DB-backed single-use token;
the Google OAuth ``state`` is a stateless HMAC nonce — this is closest to the
latter, but it carries a payload).

Design
------
* **No DB row.** The token *is* the claim: ``v1.<payload-b64url>.<sig>`` where the
  payload is ``{"sub": <subscription_id>, "scope": "digest-unsub", "exp": <ts>}``.
  The signature is ``HMAC-SHA256(secret_key, "v1." + payload-b64url)``, compared
  with :func:`hmac.compare_digest` so verification is constant-time. Flipping a
  subscription to ``off`` is idempotent, so a stateless (replayable-within-TTL)
  token is acceptable here — unlike a password reset, there is nothing to consume.
* **Single scope.** The ``scope`` field is checked on verify, so a token minted for
  some other purpose (or a future scope) can never unsubscribe a digest, and an
  unsubscribe token can only ever do this one thing.
* **Bound to one subscription.** The signature covers the subscription id, so a
  valid token for subscription A cannot be used to unsubscribe subscription B
  (any edit to the payload breaks the signature).
* **Expiry.** ``exp`` is a unix timestamp; an expired token is rejected even though
  the signature is valid. TTL is ``digest_unsubscribe_ttl_seconds`` (long, since a
  digest may sit in an inbox for weeks; low-risk because the scope is so narrow).
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import uuid
from datetime import UTC, datetime

from civicsignals_api.config import Settings, get_settings

# Token format version prefix — lets us rotate the encoding without ambiguity.
_VERSION = "v1"
# The only scope this token may carry. Verified on decode (single-purpose token).
_SCOPE = "digest-unsub"
_HMAC_ALG = "sha256"


class InvalidUnsubscribeToken(Exception):
    """The unsubscribe token failed structure/signature/scope/expiry validation."""


def _secret(settings: Settings) -> bytes:
    return settings.secret_key.encode("utf-8")


def _b64url_encode(raw: bytes) -> str:
    """URL-safe base64 without padding (so the token is link/header safe)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _sign(signing_input: str, settings: Settings) -> str:
    sig = hmac.new(_secret(settings), signing_input.encode("ascii"), _HMAC_ALG).hexdigest()
    return sig


def make_unsubscribe_token(
    subscription_id: uuid.UUID,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> str:
    """Mint a signed one-click unsubscribe token for one digest subscription.

    Returns ``v1.<payload-b64url>.<hex-sig>``. The payload binds the subscription
    id, the single ``digest-unsub`` scope, and an absolute expiry; the HMAC over
    ``v1.<payload>`` makes it tamper-proof and self-verifying (no DB lookup).
    """
    settings = settings or get_settings()
    issued = now or datetime.now(UTC)
    exp = int(issued.timestamp()) + settings.digest_unsubscribe_ttl_seconds
    payload = {"sub": str(subscription_id), "scope": _SCOPE, "exp": exp}
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{_VERSION}.{payload_b64}"
    return f"{signing_input}.{_sign(signing_input, settings)}"


def verify_unsubscribe_token(
    token: str,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> uuid.UUID:
    """Validate an unsubscribe token and return the subscription id it identifies.

    Raises :class:`InvalidUnsubscribeToken` on a malformed token, a bad signature,
    the wrong scope, an expired token, or an unparseable subscription id. The
    signature is verified before the payload is trusted, in constant time.
    """
    settings = settings or get_settings()
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidUnsubscribeToken("malformed token")
    version, payload_b64, provided_sig = parts
    if version != _VERSION:
        raise InvalidUnsubscribeToken("unsupported token version")

    expected_sig = _sign(f"{version}.{payload_b64}", settings)
    if not hmac.compare_digest(provided_sig, expected_sig):
        raise InvalidUnsubscribeToken("bad signature")

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (binascii.Error, ValueError, json.JSONDecodeError) as exc:
        raise InvalidUnsubscribeToken("undecodable payload") from exc
    if not isinstance(payload, dict):
        raise InvalidUnsubscribeToken("malformed payload")

    if payload.get("scope") != _SCOPE:
        raise InvalidUnsubscribeToken("wrong scope")

    exp = payload.get("exp")
    current = (now or datetime.now(UTC)).timestamp()
    if not isinstance(exp, int) or current > exp:
        raise InvalidUnsubscribeToken("expired token")

    try:
        return uuid.UUID(str(payload.get("sub")))
    except (ValueError, TypeError) as exc:
        raise InvalidUnsubscribeToken("bad subscription id") from exc
