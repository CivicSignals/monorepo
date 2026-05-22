"""Unit tests for the integrations framework internals (K1, no DB needed).

Covers the pieces that are pure logic / mockable: token encryption at rest,
signed OAuth state, the OAuth2 token exchange/refresh over a mocked transport,
the scope-aware HTTP-status → error mapping, and the push runner's success /
retry-backoff / dead-letter bookkeeping with a mocked provider.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from civicsignals_api.config import Settings
from civicsignals_api.modules.integrations import providers, services
from civicsignals_api.modules.integrations.models import (
    Connection,
    ConnectionStatus,
    IntegrationProviderKind,
    PushErrorCode,
    PushLog,
    PushStatus,
)
from civicsignals_api.modules.integrations.providers import (
    IntegrationProvider,
    OAuth2AuthorizationCodeMixin,
    OAuthConfig,
    ProviderError,
    PushRequest,
    PushResult,
    map_http_status_to_error_code,
)


def _settings(**overrides: Any) -> Settings:
    base = {
        "secret_key": "test-secret-key",
        "integrations_push_max_attempts": 3,
        "integrations_push_retry_base_seconds": 60,
        "integrations_push_retry_max_seconds": 3600,
        "integrations_oauth_state_ttl_seconds": 600,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Token encryption at rest
# ---------------------------------------------------------------------------


def test_token_cipher_roundtrips_and_hides_plaintext() -> None:
    cipher = services.TokenCipher("a-passphrase-derived-key")
    secret = "ya29.super-secret-oauth-token"
    ciphertext = cipher.encrypt(secret)
    assert ciphertext != secret
    assert secret not in ciphertext  # plaintext not embedded
    assert cipher.decrypt(ciphertext) == secret


def test_token_cipher_wrong_key_fails_closed() -> None:
    ciphertext = services.TokenCipher("key-one").encrypt("secret-value")
    with pytest.raises(services.IntegrationError):
        services.TokenCipher("key-two").decrypt(ciphertext)


def test_token_cipher_accepts_raw_fernet_key() -> None:
    from cryptography.fernet import Fernet

    raw = Fernet.generate_key().decode("ascii")
    cipher = services.TokenCipher(raw)
    assert cipher.decrypt(cipher.encrypt("hello")) == "hello"


# ---------------------------------------------------------------------------
# Signed OAuth state (CSRF + replay guard)
# ---------------------------------------------------------------------------


def test_oauth_state_roundtrips() -> None:
    settings = _settings()
    ws, conn = uuid.uuid4(), uuid.uuid4()
    state = services.sign_oauth_state(workspace_id=ws, connection_id=conn, settings=settings)
    parsed = services.verify_oauth_state(state, settings=settings)
    assert parsed.workspace_id == ws
    assert parsed.connection_id == conn


def test_oauth_state_rejects_tamper() -> None:
    settings = _settings()
    state = services.sign_oauth_state(
        workspace_id=uuid.uuid4(), connection_id=uuid.uuid4(), settings=settings
    )
    body, sig = state.split(".", 1)
    tampered = f"{body}.{'0' * len(sig)}"
    with pytest.raises(services.OAuthStateError):
        services.verify_oauth_state(tampered, settings=settings)


def test_oauth_state_rejects_expired() -> None:
    settings = _settings(integrations_oauth_state_ttl_seconds=0)
    state = services.sign_oauth_state(
        workspace_id=uuid.uuid4(), connection_id=uuid.uuid4(), settings=settings
    )
    # TTL 0 → already expired at verify time.
    with pytest.raises(services.OAuthStateError):
        services.verify_oauth_state(state, settings=settings)


def test_oauth_state_rejects_malformed() -> None:
    with pytest.raises(services.OAuthStateError):
        services.verify_oauth_state("not-a-valid-state", settings=_settings())


# ---------------------------------------------------------------------------
# Scope-aware error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_code,expected",
    [
        (401, PushErrorCode.AUTH),
        (403, PushErrorCode.PERMISSION),
        (404, PushErrorCode.NOT_FOUND),
        (429, PushErrorCode.RATE_LIMITED),
        (400, PushErrorCode.VALIDATION),
        (409, PushErrorCode.VALIDATION),
        (422, PushErrorCode.VALIDATION),
        (500, PushErrorCode.TRANSIENT),
        (503, PushErrorCode.TRANSIENT),
        (302, PushErrorCode.UNKNOWN),
    ],
)
def test_http_status_maps_to_error_code(status_code: int, expected: PushErrorCode) -> None:
    assert map_http_status_to_error_code(status_code) == expected


# ---------------------------------------------------------------------------
# OAuth2 token exchange / refresh over a mocked transport (injectable HTTP)
# ---------------------------------------------------------------------------


class _MockProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
    """A test provider with a configured OAuth client + mockable push."""

    kind = IntegrationProviderKind.SALESFORCE

    def oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            authorize_url="https://provider.test/authorize",
            token_url="https://provider.test/token",
            scopes=("read", "write"),
            client_id="client-123",
            client_secret="secret-456",
        )

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        return PushResult(external_id="ext-1", response={"ok": True})


def _mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_exchange_code_parses_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/token"
        return httpx.Response(
            200,
            json={
                "access_token": "access-aaa",
                "refresh_token": "refresh-bbb",
                "expires_in": 3600,
                "scope": "read write",
            },
        )

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = _MockProvider(_settings(), http)
            tokens = await prov.exchange_code(code="the-code", redirect_uri="https://cb")
            assert tokens.access_token == "access-aaa"
            assert tokens.refresh_token == "refresh-bbb"
            assert tokens.expires_at is not None
            assert set(tokens.scopes) == {"read", "write"}

    asyncio.run(_run())


def test_refresh_reuses_old_refresh_token_when_omitted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = _MockProvider(_settings(), http)
            tokens = await prov.refresh(refresh_token="old-refresh")
            assert tokens.access_token == "new-access"
            # Provider omitted refresh_token → the old one is preserved.
            assert tokens.refresh_token == "old-refresh"

    asyncio.run(_run())


def test_token_endpoint_error_maps_to_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_grant"})

    async def _run() -> None:
        async with _mock_http(handler) as http:
            prov = _MockProvider(_settings(), http)
            with pytest.raises(ProviderError) as exc_info:
                await prov.exchange_code(code="bad", redirect_uri="https://cb")
            assert exc_info.value.code == PushErrorCode.AUTH

    asyncio.run(_run())


def test_unconfigured_provider_cannot_exchange() -> None:
    class _Unconfigured(_MockProvider):
        def oauth_config(self) -> OAuthConfig:
            return OAuthConfig(
                authorize_url="https://x/a",
                token_url="https://x/t",
                scopes=(),
                client_id=None,
                client_secret=None,
            )

    async def _run() -> None:
        async with _mock_http(lambda r: httpx.Response(200, json={})) as http:
            prov = _Unconfigured(_settings(), http)
            with pytest.raises(ProviderError):
                await prov.exchange_code(code="x", redirect_uri="y")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Push runner: success / retry-backoff / dead-letter (mocked provider)
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal async session stub (the push runner only flushes)."""

    async def flush(self) -> None:  # pragma: no cover - trivial
        pass


def _connection_with_token(settings: Settings) -> Connection:
    cipher = services.token_cipher(settings)
    conn = Connection(
        workspace_id=uuid.uuid4(),
        provider=IntegrationProviderKind.SALESFORCE,
        name="Test",
        status=ConnectionStatus.HEALTHY,
    )
    conn.access_token_encrypted = cipher.encrypt("live-access-token")
    conn.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
    return conn


def _push_log() -> PushLog:
    log = PushLog(
        workspace_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        target="salesforce.opportunity",
        request={"name": "Acme"},
        status=PushStatus.PENDING,
        attempt_count=0,
    )
    return log


class _StubPushProvider(_MockProvider):
    """Provider whose ``push`` is overridden per-test (success or raise)."""

    _result: PushResult | None = None
    _error: ProviderError | None = None

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _register_stub(monkeypatch: pytest.MonkeyPatch, prov_cls: type[IntegrationProvider]) -> None:
    monkeypatch.setitem(providers.REGISTRY, IntegrationProviderKind.SALESFORCE, prov_cls)


def test_execute_push_success_records_external_id(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()

    class _OK(_StubPushProvider):
        _result = PushResult(external_id="0061T-ABC", response={"id": "0061T-ABC"})

    _register_stub(monkeypatch, _OK)
    conn = _connection_with_token(settings)
    log = _push_log()

    async def _run() -> PushLog:
        async with _mock_http(lambda r: httpx.Response(200, json={})) as http:
            return await services.execute_push(
                _FakeSession(),  # type: ignore[arg-type]
                connection=conn,
                log=log,
                request=PushRequest(target="salesforce.opportunity", payload={"name": "Acme"}),
                settings=settings,
                http_client=http,
            )

    result = asyncio.run(_run())
    assert result.status == PushStatus.SUCCESS
    assert result.external_id == "0061T-ABC"
    assert result.attempt_count == 1
    assert conn.status == ConnectionStatus.HEALTHY
    assert conn.last_push_at is not None


def test_execute_push_transient_failure_schedules_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()

    class _Boom(_StubPushProvider):
        _error = ProviderError(PushErrorCode.TRANSIENT, "503 from provider")

    _register_stub(monkeypatch, _Boom)
    conn = _connection_with_token(settings)
    log = _push_log()

    async def _run() -> PushLog:
        async with _mock_http(lambda r: httpx.Response(200, json={})) as http:
            return await services.execute_push(
                _FakeSession(),  # type: ignore[arg-type]
                connection=conn,
                log=log,
                request=PushRequest(target="salesforce.opportunity", payload={}),
                settings=settings,
                http_client=http,
            )

    result = asyncio.run(_run())
    assert result.status == PushStatus.FAILED
    assert result.error_code == PushErrorCode.TRANSIENT
    assert result.attempt_count == 1
    assert result.retry_at is not None  # scheduled for retry
    assert conn.status == ConnectionStatus.DEGRADED


def test_execute_push_validation_error_dead_letters(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()

    class _Bad(_StubPushProvider):
        _error = ProviderError(PushErrorCode.VALIDATION, "CloseDate is required")

    _register_stub(monkeypatch, _Bad)
    conn = _connection_with_token(settings)
    log = _push_log()

    async def _run() -> PushLog:
        async with _mock_http(lambda r: httpx.Response(200, json={})) as http:
            return await services.execute_push(
                _FakeSession(),  # type: ignore[arg-type]
                connection=conn,
                log=log,
                request=PushRequest(target="salesforce.opportunity", payload={}),
                settings=settings,
                http_client=http,
            )

    result = asyncio.run(_run())
    # Validation errors are non-retryable → straight to dead-letter, no retry_at.
    assert result.status == PushStatus.DEAD_LETTER
    assert result.error_code == PushErrorCode.VALIDATION
    assert result.retry_at is None


def test_execute_push_auth_error_marks_needs_reauth(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings()

    class _Auth(_StubPushProvider):
        _error = ProviderError(PushErrorCode.AUTH, "401 invalid token")

    _register_stub(monkeypatch, _Auth)
    conn = _connection_with_token(settings)
    log = _push_log()

    async def _run() -> PushLog:
        async with _mock_http(lambda r: httpx.Response(200, json={})) as http:
            return await services.execute_push(
                _FakeSession(),  # type: ignore[arg-type]
                connection=conn,
                log=log,
                request=PushRequest(target="salesforce.opportunity", payload={}),
                settings=settings,
                http_client=http,
            )

    result = asyncio.run(_run())
    assert result.error_code == PushErrorCode.AUTH
    assert conn.status == ConnectionStatus.NEEDS_REAUTH


def test_execute_push_exhausts_attempts_to_dead_letter(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(integrations_push_max_attempts=2)

    class _Boom(_StubPushProvider):
        _error = ProviderError(PushErrorCode.TRANSIENT, "still down")

    _register_stub(monkeypatch, _Boom)
    conn = _connection_with_token(settings)
    log = _push_log()
    log.attempt_count = 1  # one prior failed attempt already

    async def _run() -> PushLog:
        async with _mock_http(lambda r: httpx.Response(200, json={})) as http:
            return await services.execute_push(
                _FakeSession(),  # type: ignore[arg-type]
                connection=conn,
                log=log,
                request=PushRequest(target="salesforce.opportunity", payload={}),
                settings=settings,
                http_client=http,
            )

    result = asyncio.run(_run())
    # 2nd attempt == max_attempts → dead-letter even though transient.
    assert result.attempt_count == 2
    assert result.status == PushStatus.DEAD_LETTER
    assert result.retry_at is None


def test_ensure_fresh_access_token_refreshes_on_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired token is auto-refreshed and the new (encrypted) token returned."""
    settings = _settings()
    cipher = services.token_cipher(settings)

    refreshed: dict[str, bool] = {"called": False}

    class _Refreshing(_MockProvider):
        async def refresh(self, *, refresh_token: str) -> providers.TokenSet:
            refreshed["called"] = True
            assert refresh_token == "the-refresh-token"
            return providers.TokenSet(
                access_token="freshly-refreshed",
                refresh_token="the-refresh-token",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )

    _register_stub(monkeypatch, _Refreshing)

    conn = Connection(
        workspace_id=uuid.uuid4(),
        provider=IntegrationProviderKind.SALESFORCE,
        name="Expiring",
        status=ConnectionStatus.HEALTHY,
    )
    conn.access_token_encrypted = cipher.encrypt("expired-access-token")
    conn.refresh_token_encrypted = cipher.encrypt("the-refresh-token")
    conn.token_expires_at = datetime.now(UTC) - timedelta(minutes=5)  # expired

    async def _run() -> str:
        async with _mock_http(lambda r: httpx.Response(200, json={})) as http:
            return await services.ensure_fresh_access_token(
                _FakeSession(),  # type: ignore[arg-type]
                conn,
                settings=settings,
                http_client=http,
            )

    token = asyncio.run(_run())
    assert refreshed["called"] is True
    assert token == "freshly-refreshed"
    # The new token is persisted (encrypted) and usable again.
    assert cipher.decrypt(conn.access_token_encrypted or "") == "freshly-refreshed"


def test_ensure_fresh_access_token_no_refresh_marks_needs_reauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired token with no refresh token → needs_reauth + raises."""
    settings = _settings()
    cipher = services.token_cipher(settings)
    _register_stub(monkeypatch, _MockProvider)

    conn = Connection(
        workspace_id=uuid.uuid4(),
        provider=IntegrationProviderKind.SALESFORCE,
        name="NoRefresh",
        status=ConnectionStatus.HEALTHY,
    )
    conn.access_token_encrypted = cipher.encrypt("expired")
    conn.token_expires_at = datetime.now(UTC) - timedelta(minutes=5)

    async def _run() -> None:
        await services.ensure_fresh_access_token(
            _FakeSession(),  # type: ignore[arg-type]
            conn,
            settings=settings,
        )

    with pytest.raises(services.ConnectionNotConnectedError):
        asyncio.run(_run())
    assert conn.status == ConnectionStatus.NEEDS_REAUTH


def test_retry_backoff_is_exponential_and_capped() -> None:
    settings = _settings(
        integrations_push_retry_base_seconds=60, integrations_push_retry_max_seconds=300
    )
    assert services._retry_delay_seconds(1, settings) == 60
    assert services._retry_delay_seconds(2, settings) == 120
    assert services._retry_delay_seconds(3, settings) == 240
    # Capped at max (60 * 2**3 = 480 > 300).
    assert services._retry_delay_seconds(4, settings) == 300
