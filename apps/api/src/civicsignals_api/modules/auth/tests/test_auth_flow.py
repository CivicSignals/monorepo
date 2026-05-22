"""End-to-end auth flow tests (signup/login/logout/verify/me).

These need Postgres (CITEXT + UUID columns); the ``client`` fixture skips when no
DSN is configured. CI provides one.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from civicsignals_api.config import get_settings
from civicsignals_api.main import app
from civicsignals_api.modules.notifications import services as notifications_services

SIGNUP = "/api/v1/auth/signup"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
VERIFY = "/api/v1/auth/verify-email"
ME = "/api/v1/auth/me"

EMAIL = "Maya@Example.com"
PASSWORD = "s3cure-pa55word"


def _token_from_email(recorder: notifications_services.RecordingEmailSender) -> str:
    assert recorder.sent, "expected a verification email"
    link = recorder.sent[-1].text_body
    # Find the URL containing ?token= in the body.
    for word in link.split():
        if "token=" in word:
            qs = parse_qs(urlparse(word).query)
            return qs["token"][0]
    raise AssertionError("no verification token in email body")


def test_signup_creates_user_and_sends_verification(
    client: TestClient, recorder: notifications_services.RecordingEmailSender
) -> None:
    resp = client.post(SIGNUP, json={"email": EMAIL, "password": PASSWORD, "name": "Maya"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["email"].lower() == EMAIL.lower()
    assert body["user"]["email_verified"] is False
    assert body["tokens"]["access_token"]
    assert body["tokens"]["token_type"] == "bearer"
    assert len(recorder.sent) == 1
    # EmailStr lowercases the domain; comparison is case-insensitive (CITEXT).
    assert recorder.sent[0].to.lower() == EMAIL.lower()


def test_signup_duplicate_email_conflicts(
    client: TestClient, recorder: notifications_services.RecordingEmailSender
) -> None:
    first = client.post(SIGNUP, json={"email": EMAIL, "password": PASSWORD})
    assert first.status_code == 201
    # Case-insensitive uniqueness (CITEXT): different case still conflicts.
    dup = client.post(SIGNUP, json={"email": EMAIL.lower(), "password": PASSWORD})
    assert dup.status_code == 409
    assert dup.headers["content-type"].startswith("application/problem+json")
    assert dup.json()["title"] == "Email already registered"


def test_login_succeeds_and_me_returns_user(
    client: TestClient, recorder: notifications_services.RecordingEmailSender
) -> None:
    client.post(SIGNUP, json={"email": EMAIL, "password": PASSWORD})
    resp = client.post(LOGIN, json={"email": EMAIL, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    access = resp.json()["tokens"]["access_token"]

    me = client.get(ME, headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"].lower() == EMAIL.lower()


def test_login_wrong_password_unauthorized(
    client: TestClient, recorder: notifications_services.RecordingEmailSender
) -> None:
    client.post(SIGNUP, json={"email": EMAIL, "password": PASSWORD})
    resp = client.post(LOGIN, json={"email": EMAIL, "password": "nope-nope-nope"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["type"].endswith("/invalid_credentials")


def test_login_unknown_email_unauthorized(client: TestClient) -> None:
    resp = client.post(LOGIN, json={"email": "ghost@example.com", "password": PASSWORD})
    assert resp.status_code == 401


def test_me_without_token_unauthorized(client: TestClient) -> None:
    resp = client.get(ME)
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_me_with_garbage_token_unauthorized(client: TestClient) -> None:
    resp = client.get(ME, headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


def test_verify_email_consumes_token_once(
    client: TestClient, recorder: notifications_services.RecordingEmailSender
) -> None:
    client.post(SIGNUP, json={"email": EMAIL, "password": PASSWORD})
    token = _token_from_email(recorder)

    ok = client.post(VERIFY, json={"token": token})
    assert ok.status_code == 200, ok.text
    assert ok.json()["message"] == "email verified"

    # /me should now report verified.
    login = client.post(LOGIN, json={"email": EMAIL, "password": PASSWORD})
    access = login.json()["tokens"]["access_token"]
    me = client.get(ME, headers={"Authorization": f"Bearer {access}"})
    assert me.json()["email_verified"] is True

    # Single-use: replaying the same token fails.
    replay = client.post(VERIFY, json={"token": token})
    assert replay.status_code == 400
    assert replay.json()["type"].endswith("/invalid_verification_token")


def test_verify_email_unknown_token_rejected(client: TestClient) -> None:
    resp = client.post(VERIFY, json={"token": "totally-made-up"})
    assert resp.status_code == 400


def test_logout_returns_message(client: TestClient) -> None:
    resp = client.post(LOGOUT)
    assert resp.status_code == 200
    assert resp.json()["message"] == "logged out"


def test_login_blocked_when_verification_required(
    client: TestClient, recorder: notifications_services.RecordingEmailSender
) -> None:
    """With require_email_verification on, an unverified user cannot log in."""
    base = get_settings()
    strict = base.model_copy(update={"require_email_verification": True})
    app.dependency_overrides[get_settings] = lambda: strict
    try:
        client.post(SIGNUP, json={"email": EMAIL, "password": PASSWORD})
        blocked = client.post(LOGIN, json={"email": EMAIL, "password": PASSWORD})
        assert blocked.status_code == 403
        assert blocked.json()["type"].endswith("/email_not_verified")

        # After verifying, login succeeds.
        token = _token_from_email(recorder)
        assert client.post(VERIFY, json={"token": token}).status_code == 200
        assert client.post(LOGIN, json={"email": EMAIL, "password": PASSWORD}).status_code == 200
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_signup_validation_error_is_problem_json(client: TestClient) -> None:
    # Password too short -> 422 problem+json with field errors.
    resp = client.post(SIGNUP, json={"email": EMAIL, "password": "short"})
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 422
    assert any(e["field"] == "password" for e in body["errors"])
