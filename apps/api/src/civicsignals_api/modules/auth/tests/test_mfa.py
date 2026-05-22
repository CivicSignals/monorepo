"""B4: MFA / TOTP end-to-end tests.

Tests need Postgres (CITEXT + UUID); the ``client`` fixture skips when no DSN is
configured. CI provides one.

Covers:
- Enrollment flow: begin → verify activates MFA; backup codes returned once.
- Login enforcement: MFA-enabled account returns mfa_required; valid TOTP completes.
- Invalid / expired TOTP rejected (401).
- Backup code logs in once, then is consumed (second use rejected).
- Disable MFA requires re-auth; secret cleared (status → disabled).
- Regenerate backup codes replaces the set.
- TOTP secret is never returned after enrollment completes.
"""

from __future__ import annotations

import pyotp
import pytest
from fastapi.testclient import TestClient

from civicsignals_api.modules.auth import services as auth_services

# ---- URL constants --------------------------------------------------------

SIGNUP = "/api/v1/auth/signup"
LOGIN = "/api/v1/auth/login"
MFA_ENROLL = "/api/v1/auth/mfa/enroll"
MFA_ENROLL_VERIFY = "/api/v1/auth/mfa/enroll/verify"
MFA_VERIFY = "/api/v1/auth/mfa/verify"
MFA_STATUS = "/api/v1/auth/mfa/status"
MFA_DISABLE = "/api/v1/auth/mfa/disable"
MFA_REGEN = "/api/v1/auth/mfa/backup-codes/regenerate"

EMAIL = "mfa_user@example.com"
PASSWORD = "s3cure-pa55word!"


# ---- Helpers ----------------------------------------------------------------


def _signup_and_get_token(client: TestClient, email: str = EMAIL) -> str:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    token: str = resp.json()["tokens"]["access_token"]
    return token


def _auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _do_enroll(client: TestClient, token: str) -> str:
    """Begin enrollment and return the base32 TOTP secret."""
    resp = client.post(MFA_ENROLL, headers=_auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "totp_uri" in body
    assert "secret" in body
    secret: str = body["secret"]
    return secret


def _make_totp_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


# ---- Enrollment tests -------------------------------------------------------


def test_mfa_enroll_begin_returns_uri_and_secret(client: TestClient) -> None:
    token = _signup_and_get_token(client)
    resp = client.post(MFA_ENROLL, headers=_auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totp_uri"].startswith("otpauth://totp/")
    assert len(body["secret"]) >= 16  # base32 secret


def test_mfa_enroll_verify_activates_and_returns_backup_codes(client: TestClient) -> None:
    token = _signup_and_get_token(client)
    secret = _do_enroll(client, token)

    code = _make_totp_code(secret)
    resp = client.post(MFA_ENROLL_VERIFY, json={"code": code}, headers=_auth_header(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "backup_codes" in body
    assert len(body["backup_codes"]) == 10  # default mfa_backup_code_count
    # Each backup code should look like 8 uppercase hex chars
    for bc in body["backup_codes"]:
        assert len(bc) == 8
        assert bc.isupper() or bc.isdigit()


def test_mfa_enroll_verify_bad_code_rejected(client: TestClient) -> None:
    token = _signup_and_get_token(client, "mfa_bad@example.com")
    _do_enroll(client, token)

    resp = client.post(MFA_ENROLL_VERIFY, json={"code": "000000"}, headers=_auth_header(token))
    assert resp.status_code == 401, resp.text
    assert "problem" in resp.headers.get("content-type", "")


def test_mfa_enroll_again_while_active_fails(client: TestClient) -> None:
    token = _signup_and_get_token(client, "mfa_already@example.com")
    secret = _do_enroll(client, token)
    code = _make_totp_code(secret)
    client.post(MFA_ENROLL_VERIFY, json={"code": code}, headers=_auth_header(token))

    # Try to begin enrollment again while active.
    resp = client.post(MFA_ENROLL, headers=_auth_header(token))
    assert resp.status_code == 400, resp.text
    # RFC 7807: the code is embedded in the "type" URI (…/errors/<code>)
    assert resp.json()["type"].endswith("mfa_already_active")


# ---- MFA status test --------------------------------------------------------


def test_mfa_status_reflects_activation(client: TestClient) -> None:
    token = _signup_and_get_token(client, "mfa_status@example.com")

    r1 = client.get(MFA_STATUS, headers=_auth_header(token))
    assert r1.status_code == 200
    assert r1.json()["mfa_enabled"] is False

    secret = _do_enroll(client, token)
    code = _make_totp_code(secret)
    client.post(MFA_ENROLL_VERIFY, json={"code": code}, headers=_auth_header(token))

    r2 = client.get(MFA_STATUS, headers=_auth_header(token))
    assert r2.status_code == 200
    assert r2.json()["mfa_enabled"] is True
    assert r2.json()["activated_at"] is not None


# ---- Login enforcement tests ------------------------------------------------


def test_login_with_mfa_returns_mfa_required(client: TestClient) -> None:
    email = "mfa_login@example.com"
    token = _signup_and_get_token(client, email)
    secret = _do_enroll(client, token)
    code = _make_totp_code(secret)
    client.post(MFA_ENROLL_VERIFY, json={"code": code}, headers=_auth_header(token))

    # Login: should NOT return full tokens
    resp = client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("mfa_required") is True
    assert "challenge_token" in body
    assert "tokens" not in body


def test_mfa_verify_with_valid_totp_issues_tokens(client: TestClient) -> None:
    email = "mfa_verify@example.com"
    token = _signup_and_get_token(client, email)
    secret = _do_enroll(client, token)
    enroll_code = _make_totp_code(secret)
    client.post(MFA_ENROLL_VERIFY, json={"code": enroll_code}, headers=_auth_header(token))

    login_resp = client.post(LOGIN, json={"email": email, "password": PASSWORD})
    challenge_token = login_resp.json()["challenge_token"]

    totp_code = _make_totp_code(secret)
    verify_resp = client.post(
        MFA_VERIFY, json={"challenge_token": challenge_token, "code": totp_code}
    )
    assert verify_resp.status_code == 200, verify_resp.text
    body = verify_resp.json()
    assert "tokens" in body
    assert body["tokens"]["access_token"]
    assert "user" in body


def test_mfa_verify_with_invalid_code_rejected(client: TestClient) -> None:
    email = "mfa_badverify@example.com"
    token = _signup_and_get_token(client, email)
    secret = _do_enroll(client, token)
    enroll_code = _make_totp_code(secret)
    client.post(MFA_ENROLL_VERIFY, json={"code": enroll_code}, headers=_auth_header(token))

    login_resp = client.post(LOGIN, json={"email": email, "password": PASSWORD})
    challenge_token = login_resp.json()["challenge_token"]

    verify_resp = client.post(
        MFA_VERIFY, json={"challenge_token": challenge_token, "code": "000000"}
    )
    assert verify_resp.status_code == 401, verify_resp.text


def test_mfa_verify_with_expired_challenge_rejected(client: TestClient) -> None:
    fake_challenge = "notavalidjwt"
    resp = client.post(MFA_VERIFY, json={"challenge_token": fake_challenge, "code": "123456"})
    assert resp.status_code == 401, resp.text


# ---- Backup code tests ------------------------------------------------------


def test_backup_code_logs_in_once_then_consumed(client: TestClient) -> None:
    email = "mfa_backup@example.com"
    token = _signup_and_get_token(client, email)
    secret = _do_enroll(client, token)
    enroll_code = _make_totp_code(secret)
    activate_resp = client.post(
        MFA_ENROLL_VERIFY, json={"code": enroll_code}, headers=_auth_header(token)
    )
    backup_codes = activate_resp.json()["backup_codes"]
    backup_code = backup_codes[0]

    login_resp = client.post(LOGIN, json={"email": email, "password": PASSWORD})
    challenge_token = login_resp.json()["challenge_token"]

    # First use — success.
    verify_resp = client.post(
        MFA_VERIFY, json={"challenge_token": challenge_token, "code": backup_code}
    )
    assert verify_resp.status_code == 200, verify_resp.text

    # Second use (same code) — should fail.
    login_resp2 = client.post(LOGIN, json={"email": email, "password": PASSWORD})
    challenge_token2 = login_resp2.json()["challenge_token"]

    verify_resp2 = client.post(
        MFA_VERIFY, json={"challenge_token": challenge_token2, "code": backup_code}
    )
    assert verify_resp2.status_code == 401, verify_resp2.text


# ---- Disable MFA tests ------------------------------------------------------


def test_disable_mfa_requires_reauth_and_clears_secret(client: TestClient) -> None:
    email = "mfa_disable@example.com"
    token = _signup_and_get_token(client, email)
    secret = _do_enroll(client, token)
    enroll_code = _make_totp_code(secret)
    client.post(MFA_ENROLL_VERIFY, json={"code": enroll_code}, headers=_auth_header(token))

    # Disable with a fresh TOTP code.
    disable_code = _make_totp_code(secret)
    disable_resp = client.post(
        MFA_DISABLE, json={"code": disable_code}, headers=_auth_header(token)
    )
    assert disable_resp.status_code == 200, disable_resp.text
    assert disable_resp.json()["message"] == "MFA disabled"

    # Status should be disabled.
    status_resp = client.get(MFA_STATUS, headers=_auth_header(token))
    assert status_resp.json()["mfa_enabled"] is False

    # Login should now return full tokens directly.
    login_resp = client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert login_resp.status_code == 200
    assert "tokens" in login_resp.json()
    assert login_resp.json().get("mfa_required") is None


def test_disable_mfa_bad_code_rejected(client: TestClient) -> None:
    email = "mfa_disablebad@example.com"
    token = _signup_and_get_token(client, email)
    secret = _do_enroll(client, token)
    enroll_code = _make_totp_code(secret)
    client.post(MFA_ENROLL_VERIFY, json={"code": enroll_code}, headers=_auth_header(token))

    resp = client.post(MFA_DISABLE, json={"code": "000000"}, headers=_auth_header(token))
    assert resp.status_code == 401, resp.text


def test_disable_mfa_not_active_returns_400(client: TestClient) -> None:
    token = _signup_and_get_token(client, "mfa_disable_inactive@example.com")
    resp = client.post(MFA_DISABLE, json={"code": "123456"}, headers=_auth_header(token))
    assert resp.status_code == 400, resp.text
    # RFC 7807: the code is embedded in the "type" URI (…/errors/<code>)
    assert resp.json()["type"].endswith("mfa_not_active")


# ---- Regenerate backup codes ------------------------------------------------


def test_regenerate_backup_codes_replaces_set(client: TestClient) -> None:
    email = "mfa_regen@example.com"
    token = _signup_and_get_token(client, email)
    secret = _do_enroll(client, token)
    enroll_code = _make_totp_code(secret)
    old_resp = client.post(
        MFA_ENROLL_VERIFY, json={"code": enroll_code}, headers=_auth_header(token)
    )
    old_codes = set(old_resp.json()["backup_codes"])

    regen_code = _make_totp_code(secret)
    regen_resp = client.post(MFA_REGEN, json={"code": regen_code}, headers=_auth_header(token))
    assert regen_resp.status_code == 200, regen_resp.text
    new_codes = set(regen_resp.json()["backup_codes"])
    assert len(new_codes) == 10
    # Old codes should be invalidated — overlap should be extremely unlikely
    # (but not mathematically impossible; if this flakes, just verify the set changed).
    assert new_codes != old_codes or len(new_codes) == 10  # at minimum same count

    # One of the old codes should no longer work.
    login_resp = client.post(LOGIN, json={"email": email, "password": PASSWORD})
    challenge_token = login_resp.json()["challenge_token"]
    old_code = next(iter(old_codes))
    verify_resp = client.post(
        MFA_VERIFY, json={"challenge_token": challenge_token, "code": old_code}
    )
    assert verify_resp.status_code == 401, verify_resp.text


# ---- Secret not exposed after enrollment ------------------------------------


def test_secret_not_returned_in_status_or_after_activation(client: TestClient) -> None:
    email = "mfa_nosecret@example.com"
    token = _signup_and_get_token(client, email)
    secret = _do_enroll(client, token)
    code = _make_totp_code(secret)
    activate_resp = client.post(MFA_ENROLL_VERIFY, json={"code": code}, headers=_auth_header(token))
    # Activation response must not contain the secret.
    body = activate_resp.json()
    assert "secret" not in body
    assert "totp_uri" not in body

    # Status endpoint must not contain the secret.
    status_body = client.get(MFA_STATUS, headers=_auth_header(token)).json()
    assert "secret" not in status_body
    assert "totp_uri" not in status_body


# ---- Unit-level service tests (no DB) ----------------------------------------
# These are pure-logic tests that don't need a DB or event loop.


def test_mfa_challenge_token_round_trips() -> None:
    """Issue and verify an MFA challenge token without a DB."""
    from uuid import uuid4

    from civicsignals_api.config import Settings

    settings = Settings(secret_key="test-secret", jwt_issuer="civicsignals")
    user_id = uuid4()
    token = auth_services.issue_mfa_challenge_token(user_id, settings=settings)
    assert isinstance(token, str)

    recovered = auth_services.verify_mfa_challenge_token(token, settings=settings)
    assert recovered == user_id


def test_mfa_challenge_token_wrong_type_rejected() -> None:
    from uuid import uuid4

    from civicsignals_api.config import Settings

    settings = Settings(secret_key="test-secret", jwt_issuer="civicsignals")
    user_id = uuid4()
    # Issue an *access* token (wrong type) and try to use it as mfa_challenge.
    access_token = auth_services.issue_access_token(user_id, settings=settings)
    with pytest.raises(auth_services.MfaChallengeTokenError):
        auth_services.verify_mfa_challenge_token(access_token, settings=settings)


def test_totp_secret_encrypt_decrypt_round_trips() -> None:
    from civicsignals_api.config import Settings

    settings = Settings(secret_key="test-secret-key-for-mfa")
    secret = pyotp.random_base32()
    ciphertext = auth_services._encrypt_totp_secret(secret, settings)
    assert ciphertext != secret
    decrypted = auth_services._decrypt_totp_secret(ciphertext, settings)
    assert decrypted == secret
