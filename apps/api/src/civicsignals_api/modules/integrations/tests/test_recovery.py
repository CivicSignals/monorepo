"""Unit tests for K5 push-failure recovery (no DB, no live CRM required).

Covers the recovery service surface that is pure logic / mockable:

- Error-code → human-readable diagnosis mapping (inline cause + CTA flags).
- ``retry_push_log`` replays a failed row's stored payload/target/key and routes
  through the K4 idempotent path: a retry of a push that already succeeded
  externally calls the provider's *update* path (no duplicate CRM object).
- ``retry_push_log`` of a now-fixed push succeeds and upserts the idempotency
  registry; a fresh push-log row is appended (the original is left untouched).
- Retry of a still-broken push records the typed error / dead-letters as usual.

API-level RBAC + workspace-scoping are exercised in ``test_recovery_api.py``
(live DB; skips without a DSN).
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
    PushIdempotency,
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
)

# ---------------------------------------------------------------------------
# Helpers (mirror test_idempotent_push.py's mock provider + fake session)
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "secret_key": "test-secret-key",
        "integrations_push_max_attempts": 3,
        "integrations_push_retry_base_seconds": 60,
        "integrations_push_retry_max_seconds": 3600,
        "integrations_oauth_state_ttl_seconds": 600,
    }
    base.update(overrides)
    return Settings(**base)


def _mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _noop_http() -> httpx.AsyncClient:
    return _mock_http(lambda r: httpx.Response(200, json={}))


def _connection(settings: Settings) -> Connection:
    cipher = services.token_cipher(settings)
    conn = Connection(
        workspace_id=uuid.uuid4(),
        provider=IntegrationProviderKind.SALESFORCE,
        name="Test",
        status=ConnectionStatus.DEGRADED,
    )
    conn.id = uuid.uuid4()
    conn.access_token_encrypted = cipher.encrypt("live-access-token")
    conn.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
    return conn


class _MockProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
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


def _register_provider(
    monkeypatch: pytest.MonkeyPatch, prov_cls: type[IntegrationProvider]
) -> None:
    monkeypatch.setitem(providers.REGISTRY, IntegrationProviderKind.SALESFORCE, prov_cls)


_PUSH_IDEMPOTENCY_TABLE = "integrations_push_idempotency"


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeSession:
    """Minimal async-session stub tracking added rows + the idempotency registry.

    Mirrors the fake session used by the K4 idempotent-push tests: ORM SELECTs for
    ``PushIdempotency`` resolve against ``_idempotency``; ``pg_insert`` upserts
    record into the same dict. ``add`` collects the appended push-log rows so a
    test can assert a *new* row was created on retry.
    """

    def __init__(self) -> None:
        self._rows: list[Any] = []
        self._flush_count = 0
        self._idempotency: dict[tuple[Any, str], PushIdempotency] = {}

    def add(self, obj: Any) -> None:
        self._rows.append(obj)

    async def flush(self) -> None:
        self._flush_count += 1

    async def rollback(self) -> None:  # pragma: no cover - not hit on the happy path
        pass

    async def execute(self, stmt: Any) -> _FakeResult:
        if type(stmt).__name__ == "Insert":
            try:
                if getattr(stmt.table, "name", "") == _PUSH_IDEMPOTENCY_TABLE:
                    params = stmt.compile().params
                    conn_id = params.get("connection_id")
                    key = params.get("idempotency_key")
                    ext_id = params.get("external_id")
                    if conn_id is not None and key is not None and ext_id is not None:
                        record = self._idempotency.get((conn_id, key))
                        if record is None:
                            self._idempotency[(conn_id, key)] = PushIdempotency(
                                connection_id=conn_id,
                                idempotency_key=key,
                                external_id=ext_id,
                            )
                        else:
                            record.external_id = ext_id
            except Exception:  # pragma: no cover - defensive
                pass
            return _FakeResult(None)

        try:
            descs = stmt.column_descriptions
            if descs and descs[0].get("entity") is PushIdempotency:
                rows = list(self._idempotency.values())
                return _FakeResult(rows[-1] if rows else None)
        except AttributeError:  # pragma: no cover - defensive
            pass
        return _FakeResult(None)


def _failed_log(conn: Connection, *, code: PushErrorCode, key: str | None) -> PushLog:
    log = PushLog(
        workspace_id=conn.workspace_id,
        connection_id=conn.id,
        target="salesforce.Opportunity",
        request={"Name": "Acme RFP"},
        signal_id="sig-1",
        status=PushStatus.FAILED,
        attempt_count=1,
        idempotency_key=key,
        error_code=code,
        error_message="boom",
    )
    log.id = uuid.uuid4()
    return log


# ---------------------------------------------------------------------------
# Diagnosis mapping (pure)
# ---------------------------------------------------------------------------


def test_diagnose_push_error_none_returns_none() -> None:
    assert services.diagnose_push_error(None) is None


@pytest.mark.parametrize("code", list(PushErrorCode))
def test_diagnose_push_error_has_human_cause_for_every_code(code: PushErrorCode) -> None:
    diag = services.diagnose_push_error(code)
    assert diag is not None
    assert diag.code is code
    assert diag.cause and diag.recommended_action  # non-empty operator-facing text


def test_diagnose_auth_flags_needs_reauth() -> None:
    diag = services.diagnose_push_error(PushErrorCode.AUTH)
    assert diag is not None
    assert diag.needs_reauth is True
    assert diag.retryable is True


def test_diagnose_validation_is_not_retryable() -> None:
    diag = services.diagnose_push_error(PushErrorCode.VALIDATION)
    assert diag is not None
    assert diag.retryable is False
    assert diag.needs_reauth is False


def test_diagnose_transient_is_retryable_no_reauth() -> None:
    diag = services.diagnose_push_error(PushErrorCode.TRANSIENT)
    assert diag is not None
    assert diag.retryable is True
    assert diag.needs_reauth is False


# ---------------------------------------------------------------------------
# Retry routes through the K4 idempotent path
# ---------------------------------------------------------------------------


def test_retry_now_fixed_push_succeeds_and_appends_new_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retry of a transient failure that now succeeds: SUCCESS + a fresh row."""
    settings = _settings()

    class _NowOK(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            return PushResult(external_id="sf-fixed", created=True)

    _register_provider(monkeypatch, _NowOK)
    conn = _connection(settings)
    session = _FakeSession()
    original = _failed_log(conn, code=PushErrorCode.TRANSIENT, key="stable-key")

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.retry_push_log(
                session,  # type: ignore[arg-type]
                connection=conn,
                log=original,
                settings=settings,
                http_client=http,
            )

    retry_log = asyncio.run(_run())
    assert retry_log.status == PushStatus.SUCCESS
    assert retry_log.external_id == "sf-fixed"
    # A NEW push-log row was appended (the original is preserved as audit).
    assert retry_log is not original
    assert original.status == PushStatus.FAILED
    appended = [r for r in session._rows if isinstance(r, PushLog)]
    assert len(appended) == 1 and appended[0] is retry_log
    # Success upserted the canonical external-id for the key.
    assert session._idempotency[(conn.id, "stable-key")].external_id == "sf-fixed"


def test_retry_of_already_succeeded_push_updates_not_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """K4 reuse: if the prior push actually created the object (external_id known),
    the retry routes through the provider's UPDATE path — never a duplicate create."""
    settings = _settings()
    create_calls: list[str] = []
    update_calls: list[str] = []

    class _Upsert(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            if request.external_id:
                update_calls.append(request.external_id)
                return PushResult(external_id=request.external_id, created=False)
            create_calls.append("POST")
            return PushResult(external_id="sf-new", created=True)

    _register_provider(monkeypatch, _Upsert)
    conn = _connection(settings)
    session = _FakeSession()
    # The original push log recorded a failure (e.g. timeout), but the object was
    # in fact created and the canonical external-id is registered in PushIdempotency.
    session._idempotency[(conn.id, "stable-key")] = PushIdempotency(
        connection_id=conn.id,
        idempotency_key="stable-key",
        external_id="sf-001",
    )
    original = _failed_log(conn, code=PushErrorCode.TRANSIENT, key="stable-key")

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.retry_push_log(
                session,  # type: ignore[arg-type]
                connection=conn,
                log=original,
                settings=settings,
                http_client=http,
            )

    retry_log = asyncio.run(_run())
    assert retry_log.status == PushStatus.SUCCESS
    assert retry_log.external_id == "sf-001"
    assert len(update_calls) == 1, "retry must route through the UPDATE path (idempotent)"
    assert len(create_calls) == 0, "retry must NOT create a duplicate CRM object"


def test_retry_replays_stored_payload_and_target(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry replays the stored push-log payload + target (no re-mapping)."""
    settings = _settings()
    seen: dict[str, Any] = {}

    class _Echo(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            # The enriched payload carries a control key the provider pops; assert
            # the original mapped fields are preserved verbatim.
            seen["target"] = request.target
            seen["payload"] = {k: v for k, v in request.payload.items() if not k.startswith("__")}
            seen["key"] = request.idempotency_key
            return PushResult(external_id="sf-echo", created=True)

    _register_provider(monkeypatch, _Echo)
    conn = _connection(settings)
    session = _FakeSession()
    original = _failed_log(conn, code=PushErrorCode.TRANSIENT, key="stable-key")
    original.request = {"Name": "Acme RFP", "Amount": 1000}

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.retry_push_log(
                session,  # type: ignore[arg-type]
                connection=conn,
                log=original,
                settings=settings,
                http_client=http,
            )

    asyncio.run(_run())
    assert seen["target"] == "salesforce.Opportunity"
    assert seen["payload"] == {"Name": "Acme RFP", "Amount": 1000}
    assert seen["key"] == "stable-key"


def test_retry_still_broken_records_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A retry that still fails records the scope-aware typed error on the new row."""
    settings = _settings()

    class _StillAuth(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            raise ProviderError(PushErrorCode.AUTH, "401 still invalid")

    _register_provider(monkeypatch, _StillAuth)
    conn = _connection(settings)
    session = _FakeSession()
    original = _failed_log(conn, code=PushErrorCode.AUTH, key="stable-key")

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.retry_push_log(
                session,  # type: ignore[arg-type]
                connection=conn,
                log=original,
                settings=settings,
                http_client=http,
            )

    retry_log = asyncio.run(_run())
    assert retry_log.error_code == PushErrorCode.AUTH
    # Auth failure flips the connection to needs_reauth (recovery surfaces this).
    assert conn.status == ConnectionStatus.NEEDS_REAUTH
    # No idempotency row written on failure.
    assert (conn.id, "stable-key") not in session._idempotency


def test_retry_without_idempotency_key_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A push-log row with no idempotency key (no source id) still retries fine."""
    settings = _settings()

    class _OK(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            assert request.external_id is None  # nothing to look up
            return PushResult(external_id="sf-nokey", created=True)

    _register_provider(monkeypatch, _OK)
    conn = _connection(settings)
    session = _FakeSession()
    original = _failed_log(conn, code=PushErrorCode.TRANSIENT, key=None)
    original.signal_id = None

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.retry_push_log(
                session,  # type: ignore[arg-type]
                connection=conn,
                log=original,
                settings=settings,
                http_client=http,
            )

    retry_log = asyncio.run(_run())
    assert retry_log.status == PushStatus.SUCCESS
    # No key → nothing upserted into the idempotency registry.
    assert session._idempotency == {}
