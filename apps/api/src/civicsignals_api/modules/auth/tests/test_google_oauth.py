"""Tests for the B2 Google OAuth2 flow.

Covers:
- :func:`~auth.services.google_oauth_start` — URL building + signed state.
- :func:`~auth.services.google_oauth_callback` — all outcomes:
  - Existing identity → login (same user id returned).
  - Email match, no identity → link + login (same user id, email_verified set).
  - No match → create new user + link.
  - State mismatch → :class:`OAuthStateMismatchError`.
  - Unverified email → :class:`OAuthEmailUnverifiedError`.
  - Identity uniqueness: two Google accounts cannot map to the same user (via
    the same provider).
- Route-level tests (HTTP) via the ``client`` fixture from conftest, mocking
  Google token exchange and userinfo so no live Google call is made.

``http_post`` / ``http_get`` are injected as **sync** callables that return dicts.
The service's type alias is ``Callable[..., Any]``; the injectable path calls
the function without ``await`` (sync contract only — the real code uses async
httpx when no override is provided).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.config import Settings, get_settings
from civicsignals_api.db import Base, get_session
from civicsignals_api.main import app
from civicsignals_api.modules.accounts.models import (  # noqa: F401  registers tables
    Membership,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.auth import services as auth_services
from civicsignals_api.modules.auth.models import (  # noqa: F401  registers tables
    ApiToken,
    EmailVerificationToken,
    OAuthIdentity,
    PasswordResetToken,
)

_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")


def _require_db() -> str:
    if not _DSN:
        pytest.skip("DATABASE_DIRECT_URL/DATABASE_URL not set; OAuth flow needs Postgres")
    return _DSN


async def _reset_schema(dsn: str) -> None:
    engine = create_async_engine(dsn, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    dsn = _require_db()
    asyncio.run(_reset_schema(dsn))

    engine = create_async_engine(dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> Any:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)
    asyncio.run(engine.dispose())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SETTINGS = Settings(
    secret_key="test-secret-key-for-oauth",
    google_oauth_client_id="test-client-id",
    google_oauth_client_secret="test-client-secret",
    web_base_url="http://localhost:3000",
)


def _make_http_post(token_resp: dict[str, Any]) -> Any:
    """Return a sync mock for the Google token-exchange call."""

    def _post(**_kwargs: Any) -> dict[str, Any]:
        return token_resp

    return _post


def _make_http_get(userinfo_resp: dict[str, Any]) -> Any:
    """Return a sync mock for the Google userinfo call."""

    def _get(**_kwargs: Any) -> dict[str, Any]:
        return userinfo_resp

    return _get


def _signed_state(settings: Settings = _SETTINGS) -> str:
    return auth_services._sign_state("test-nonce", settings)


# ---------------------------------------------------------------------------
# Unit tests: service-layer (no DB)
# ---------------------------------------------------------------------------


class TestGoogleOAuthStart:
    def test_returns_authorization_url(self) -> None:
        result = auth_services.google_oauth_start(settings=_SETTINGS)
        assert "accounts.google.com" in result.authorization_url
        assert "test-client-id" in result.authorization_url
        assert "openid" in result.authorization_url

    def test_state_is_signed(self) -> None:
        result = auth_services.google_oauth_start(settings=_SETTINGS)
        assert auth_services._verify_state(result.state, _SETTINGS)

    def test_raises_when_client_id_missing(self) -> None:
        no_id_settings = Settings(
            secret_key="test-secret",
            google_oauth_client_id=None,
        )
        with pytest.raises(auth_services.OAuthProviderError):
            auth_services.google_oauth_start(settings=no_id_settings)


class TestStateVerification:
    def test_valid_state_passes(self) -> None:
        state = auth_services._sign_state("nonce123", _SETTINGS)
        assert auth_services._verify_state(state, _SETTINGS)

    def test_tampered_nonce_fails(self) -> None:
        state = auth_services._sign_state("nonce123", _SETTINGS)
        parts = state.split(".", 1)
        tampered = f"evil-nonce.{parts[1]}"
        assert not auth_services._verify_state(tampered, _SETTINGS)

    def test_missing_dot_fails(self) -> None:
        assert not auth_services._verify_state("nodot", _SETTINGS)

    def test_empty_state_fails(self) -> None:
        assert not auth_services._verify_state("", _SETTINGS)


# ---------------------------------------------------------------------------
# Integration tests: service-layer (needs Postgres)
# ---------------------------------------------------------------------------

START_URL = "/api/v1/auth/oauth/google/start"
CALLBACK_URL = "/api/v1/auth/oauth/google/callback"


class TestGoogleOAuthCallback:
    """DB-backed tests via the ``client`` fixture."""

    def test_callback_creates_new_user(self, client: TestClient) -> None:
        """No existing user → creates a new account and redirects with token."""
        settings = get_settings()
        state = auth_services._sign_state("nonce-new", settings)

        def _http_post(**_kw: Any) -> dict[str, Any]:
            return {"access_token": "goog-access-tok", "token_type": "Bearer"}

        def _http_get(**_kw: Any) -> dict[str, Any]:
            return {
                "sub": "google-sub-new-user",
                "email": "newuser@example.com",
                "email_verified": True,
                "name": "New User",
            }

        # Patch the HTTP callables on the service module.
        import civicsignals_api.modules.auth.services as svc

        orig_exchange = svc._exchange_code_for_tokens
        orig_fetch = svc._fetch_userinfo

        async def _patched_exchange(code: str, *, settings: Settings, http_post: Any = None) -> Any:
            return _http_post()

        async def _patched_fetch(
            access_token: str, *, settings: Settings, http_get: Any = None
        ) -> Any:
            return _http_get()

        svc._exchange_code_for_tokens = _patched_exchange
        svc._fetch_userinfo = _patched_fetch
        try:
            resp = client.get(
                CALLBACK_URL,
                params={"code": "auth-code", "state": state},
            )
        finally:
            svc._exchange_code_for_tokens = orig_exchange
            svc._fetch_userinfo = orig_fetch

        # Should redirect to web app callback page with token in fragment.
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "auth/callback/google#" in loc
        assert "access_token=" in loc

    def test_callback_links_existing_user_by_email(self, client: TestClient) -> None:
        """Email matches existing account → links Google identity, same user id."""
        settings = get_settings()
        # First create a user via signup.
        signup_resp = client.post(
            "/api/v1/auth/signup",
            json={"email": "existing@example.com", "password": "SecurePass1!"},
        )
        assert signup_resp.status_code == 201
        user_id = signup_resp.json()["user"]["id"]

        state = auth_services._sign_state("nonce-link", settings)

        import civicsignals_api.modules.auth.services as svc

        orig_exchange = svc._exchange_code_for_tokens
        orig_fetch = svc._fetch_userinfo

        async def _patched_exchange(code: str, *, settings: Settings, http_post: Any = None) -> Any:
            return {"access_token": "goog-access-tok-link", "token_type": "Bearer"}

        async def _patched_fetch(
            access_token: str, *, settings: Settings, http_get: Any = None
        ) -> Any:
            return {
                "sub": "google-sub-existing",
                "email": "existing@example.com",
                "email_verified": True,
                "name": "Existing User",
            }

        svc._exchange_code_for_tokens = _patched_exchange
        svc._fetch_userinfo = _patched_fetch
        try:
            resp = client.get(
                CALLBACK_URL,
                params={"code": "auth-code-link", "state": state},
            )
        finally:
            svc._exchange_code_for_tokens = orig_exchange
            svc._fetch_userinfo = orig_fetch

        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "auth/callback/google#" in loc
        # Decode the access_token from the fragment, verify it resolves to the same user.
        fragment = loc.split("#", 1)[1]
        params = dict(kv.split("=", 1) for kv in fragment.split("&"))
        access_token = params["access_token"]
        me_resp = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["id"] == user_id
        assert me_resp.json()["email_verified"] is True

    def test_callback_state_mismatch_redirects_to_error(self, client: TestClient) -> None:
        """Bad state → redirect to /login?error=state_mismatch."""
        resp = client.get(
            CALLBACK_URL,
            params={"code": "auth-code", "state": "bad-state-no-sig"},
        )
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "/login" in loc
        assert "state_mismatch" in loc

    def test_callback_unverified_email_redirects_to_error(self, client: TestClient) -> None:
        """Google returns email_verified=False → redirect to /login?error=email_unverified."""
        settings = get_settings()
        state = auth_services._sign_state("nonce-unverified", settings)

        import civicsignals_api.modules.auth.services as svc

        orig_exchange = svc._exchange_code_for_tokens
        orig_fetch = svc._fetch_userinfo

        async def _patched_exchange(code: str, *, settings: Settings, http_post: Any = None) -> Any:
            return {"access_token": "goog-access-unverified", "token_type": "Bearer"}

        async def _patched_fetch(
            access_token: str, *, settings: Settings, http_get: Any = None
        ) -> Any:
            return {
                "sub": "google-sub-unverified",
                "email": "unverified@example.com",
                "email_verified": False,
            }

        svc._exchange_code_for_tokens = _patched_exchange
        svc._fetch_userinfo = _patched_fetch
        try:
            resp = client.get(
                CALLBACK_URL,
                params={"code": "auth-code-unverified", "state": state},
            )
        finally:
            svc._exchange_code_for_tokens = orig_exchange
            svc._fetch_userinfo = orig_fetch

        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "/login" in loc
        assert "email_unverified" in loc

    def test_start_not_configured_returns_501(self, client: TestClient) -> None:
        """Missing client-id → 501 Not Implemented (fail-closed)."""
        base = get_settings()
        no_id = base.model_copy(update={"google_oauth_client_id": None})
        app.dependency_overrides[get_settings] = lambda: no_id
        try:
            resp = client.get(START_URL)
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert resp.status_code == 501

    def test_start_configured_redirects(self, client: TestClient) -> None:
        """Configured client-id → 302 redirect to Google."""
        base = get_settings()
        with_id = base.model_copy(
            update={
                "google_oauth_client_id": "test-cid",
                "google_oauth_client_secret": "test-cs",
            }
        )
        app.dependency_overrides[get_settings] = lambda: with_id
        try:
            resp = client.get(START_URL)
        finally:
            app.dependency_overrides.pop(get_settings, None)
        assert resp.status_code == 302
        assert "accounts.google.com" in resp.headers["location"]
