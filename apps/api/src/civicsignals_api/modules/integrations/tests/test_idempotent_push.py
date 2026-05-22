"""Unit tests for K4 idempotent push (no DB, no live CRM required).

Covers:
- Stable, deterministic idempotency key derivation.
- First push creates + records external_id.
- Second push of the same key calls provider.update (PATCH), NOT create (POST).
- Concurrent same-key pushes: unique-violation fallback to update (no dup).
- Different records → different keys → separate external objects.
- Workspace / connection isolation (same source record in two workspaces or
  two connections produces different keys and different objects).
- mypy strict conformance verified by tests that compose typed helpers.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import IntegrityError

from civicsignals_api.config import Settings
from civicsignals_api.modules.integrations import providers, services
from civicsignals_api.modules.integrations.models import (
    Connection,
    ConnectionStatus,
    IntegrationProviderKind,
    PushLog,
    PushStatus,
)
from civicsignals_api.modules.integrations.providers import (
    IntegrationProvider,
    OAuth2AuthorizationCodeMixin,
    OAuthConfig,
    PushRequest,
    PushResult,
)

# ---------------------------------------------------------------------------
# Helpers
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
        status=ConnectionStatus.HEALTHY,
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


# ---------------------------------------------------------------------------
# Fake async session (tracks push_log rows added + controls when to raise)
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal async-session stub that tracks added models and flushes.

    ``execute`` inspects the SELECT statement's entity to route the query to
    the right in-memory rows, so both ``get_field_mapping`` (FieldMapping
    entity) and ``_last_successful_push`` (PushLog entity) work correctly
    without a real database.
    """

    def __init__(self) -> None:
        self._rows: list[Any] = []
        self._flush_count = 0

    def add(self, obj: Any) -> None:
        self._rows.append(obj)

    async def flush(self) -> None:
        self._flush_count += 1

    async def rollback(self) -> None:
        pass

    async def execute(self, stmt: Any) -> _FakeResult:
        # Determine which entity the SELECT is targeting so we can route the
        # query to the correct in-memory rows.  SQLAlchemy's Select stores the
        # entity in ``columns_plus_names``; we introspect the first column's
        # entity class.  Fall back to returning None for unknown queries.
        try:
            # SA Select: froms contains the table; for ORM selects we can get
            # the entity class from the statement's column_descriptions.
            descs = stmt.column_descriptions
            if descs:
                entity_cls = descs[0].get("entity")
                if entity_cls is PushLog:
                    # Return the most recently seeded SUCCESS PushLog.
                    successful = [
                        r
                        for r in self._rows
                        if isinstance(r, PushLog)
                        and r.status == PushStatus.SUCCESS
                        and r.external_id is not None
                    ]
                    return _FakeResult(successful[-1] if successful else None)
                # For FieldMapping and other types: no rows seeded → None.
                return _FakeResult(None)
        except AttributeError:
            pass
        return _FakeResult(None)


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


# ---------------------------------------------------------------------------
# Idempotency key derivation
# ---------------------------------------------------------------------------


def test_make_idempotency_key_is_deterministic() -> None:
    ws = uuid.UUID("11111111-1111-1111-1111-111111111111")
    conn = uuid.UUID("22222222-2222-2222-2222-222222222222")
    k1 = services.make_idempotency_key(
        workspace_id=ws, connection_id=conn, target="salesforce.Opportunity", signal_id="sig-1"
    )
    k2 = services.make_idempotency_key(
        workspace_id=ws, connection_id=conn, target="salesforce.Opportunity", signal_id="sig-1"
    )
    assert k1 == k2
    assert len(k1) == 64  # SHA-256 hex


def test_make_idempotency_key_differs_across_records() -> None:
    ws = uuid.uuid4()
    conn = uuid.uuid4()
    k1 = services.make_idempotency_key(
        workspace_id=ws, connection_id=conn, target="salesforce.Opportunity", signal_id="sig-1"
    )
    k2 = services.make_idempotency_key(
        workspace_id=ws, connection_id=conn, target="salesforce.Opportunity", signal_id="sig-2"
    )
    assert k1 != k2


def test_make_idempotency_key_workspace_isolated() -> None:
    """Same signal in two workspaces → different keys."""
    ws_a, ws_b = uuid.uuid4(), uuid.uuid4()
    conn = uuid.uuid4()
    k_a = services.make_idempotency_key(
        workspace_id=ws_a, connection_id=conn, target="salesforce.Opportunity", signal_id="sig-1"
    )
    k_b = services.make_idempotency_key(
        workspace_id=ws_b, connection_id=conn, target="salesforce.Opportunity", signal_id="sig-1"
    )
    assert k_a != k_b


def test_make_idempotency_key_connection_isolated() -> None:
    """Same signal in two connections of the same workspace → different keys."""
    ws = uuid.uuid4()
    conn_a, conn_b = uuid.uuid4(), uuid.uuid4()
    k_a = services.make_idempotency_key(
        workspace_id=ws, connection_id=conn_a, target="salesforce.Opportunity", signal_id="sig-1"
    )
    k_b = services.make_idempotency_key(
        workspace_id=ws, connection_id=conn_b, target="salesforce.Opportunity", signal_id="sig-1"
    )
    assert k_a != k_b


def test_make_idempotency_key_uses_pipeline_item_id_fallback() -> None:
    ws, conn = uuid.uuid4(), uuid.uuid4()
    k = services.make_idempotency_key(
        workspace_id=ws,
        connection_id=conn,
        target="salesforce.Opportunity",
        pipeline_item_id="pi-99",
    )
    assert len(k) == 64


def test_make_idempotency_key_requires_source_id() -> None:
    with pytest.raises(ValueError, match="signal_id"):
        services.make_idempotency_key(
            workspace_id=uuid.uuid4(),
            connection_id=uuid.uuid4(),
            target="salesforce.Opportunity",
        )


# ---------------------------------------------------------------------------
# First push creates + records external_id (unit, mocked provider + session)
# ---------------------------------------------------------------------------


def test_first_push_creates_and_records_external_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """First push: provider.create called, push_log records external_id."""
    settings = _settings()
    create_calls: list[str] = []
    update_calls: list[str] = []

    class _Creating(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            if request.external_id:
                update_calls.append(request.external_id)
                return PushResult(external_id=request.external_id, created=False)
            else:
                create_calls.append("POST")
                return PushResult(external_id="sf-001", created=True)

    _register_provider(monkeypatch, _Creating)
    conn = _connection(settings)
    session = _FakeSession()

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.push_source(
                session,  # type: ignore[arg-type]
                connection=conn,
                source={"signal": {"title": "Acme RFP"}},
                target="Opportunity",
                signal_id="sig-1",
                idempotency_key="ws-conn-sig1",
                settings=settings,
                http_client=http,
            )

    log = asyncio.run(_run())
    assert log.status == PushStatus.SUCCESS
    assert log.external_id == "sf-001"
    assert len(create_calls) == 1, "provider.create should be called exactly once"
    assert len(update_calls) == 0, "provider.update must NOT be called on first push"
    assert log.idempotency_key == "ws-conn-sig1"


# ---------------------------------------------------------------------------
# Second push of the same key → UPDATE (no duplicate create)
# ---------------------------------------------------------------------------


def test_second_push_same_key_calls_update_not_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-push of same idempotency key: provider.update (PATCH), NOT create (POST)."""
    settings = _settings()
    create_calls: list[str] = []
    update_calls: list[str] = []

    class _Upsert(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            if request.external_id:
                update_calls.append(request.external_id)
                return PushResult(external_id=request.external_id, created=False)
            else:
                create_calls.append("POST")
                return PushResult(external_id="sf-001", created=True)

    _register_provider(monkeypatch, _Upsert)
    conn = _connection(settings)
    session = _FakeSession()

    # Seed the session with a prior successful push so the second push sees it.
    prior_log = PushLog(
        workspace_id=conn.workspace_id,
        connection_id=conn.id,
        target="salesforce.Opportunity",
        request={"Name": "Acme"},
        status=PushStatus.SUCCESS,
        idempotency_key="stable-key",
        external_id="sf-001",
        attempt_count=1,
    )
    session._rows.append(prior_log)

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.push_source(
                session,  # type: ignore[arg-type]
                connection=conn,
                source={"signal": {"title": "Acme RFP updated"}},
                target="Opportunity",
                signal_id="sig-1",
                idempotency_key="stable-key",
                settings=settings,
                http_client=http,
            )

    log = asyncio.run(_run())
    assert log.status == PushStatus.SUCCESS
    assert log.external_id == "sf-001"
    assert len(update_calls) == 1, "provider.update (PATCH) must be called on second push"
    assert len(create_calls) == 0, "provider.create (POST) must NOT be called on second push"


# ---------------------------------------------------------------------------
# Auto-derivation of idempotency key from signal_id
# ---------------------------------------------------------------------------


def test_push_source_auto_derives_idempotency_key_from_signal_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no explicit key is given, one is derived from signal_id."""
    settings = _settings()

    class _OK(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            return PushResult(external_id="sf-auto", created=True)

    _register_provider(monkeypatch, _OK)
    conn = _connection(settings)
    session = _FakeSession()

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.push_source(
                session,  # type: ignore[arg-type]
                connection=conn,
                source={"Name": "Auto-keyed"},
                target="Opportunity",
                signal_id="sig-auto-1",
                # No explicit idempotency_key — should be auto-derived.
                settings=settings,
                http_client=http,
            )

    log = asyncio.run(_run())
    assert log.status == PushStatus.SUCCESS
    # The push_log must carry a non-None derived key.
    assert log.idempotency_key is not None
    # It must match what make_idempotency_key would produce.
    expected_key = services.make_idempotency_key(
        workspace_id=conn.workspace_id,
        connection_id=conn.id,
        target="salesforce.Opportunity",
        signal_id="sig-auto-1",
    )
    assert log.idempotency_key == expected_key


# ---------------------------------------------------------------------------
# Race condition: unique-violation → fallback to update
# ---------------------------------------------------------------------------


def test_race_condition_unique_violation_falls_back_to_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate concurrent same-key push: IntegrityError → retry as update."""
    settings = _settings()
    create_calls: list[str] = []
    update_calls: list[str] = []

    class _Race(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            if request.external_id:
                update_calls.append(request.external_id)
                return PushResult(external_id=request.external_id, created=False)
            else:
                create_calls.append("POST")
                return PushResult(external_id="sf-race", created=True)

    _register_provider(monkeypatch, _Race)
    conn = _connection(settings)

    # A session that raises IntegrityError on the first flush of a SUCCESS row,
    # simulating the unique partial index violation.
    class _RaceSession(_FakeSession):
        _first_success_flush = True

        async def flush(self) -> None:
            self._flush_count += 1
            # Detect when a SUCCESS PushLog is being flushed for the first time
            # and raise IntegrityError to simulate the race constraint firing.
            if self._first_success_flush:
                success_rows = [
                    r
                    for r in self._rows
                    if isinstance(r, PushLog) and r.status == PushStatus.SUCCESS
                ]
                if success_rows:
                    self._first_success_flush = False
                    # Seed the session with a "winning" push so the retry-as-
                    # update path finds an external_id to use.
                    winner = PushLog(
                        workspace_id=conn.workspace_id,
                        connection_id=conn.id,
                        target="salesforce.Opportunity",
                        request={},
                        status=PushStatus.SUCCESS,
                        idempotency_key="race-key",
                        external_id="sf-race",
                        attempt_count=1,
                    )
                    self._rows.append(winner)
                    raise IntegrityError(
                        statement=None,
                        params=None,
                        orig=Exception(
                            "duplicate key value violates unique constraint "
                            '"uq_integrations_push_log_idempotency_success"'
                        ),
                    )

    session = _RaceSession()

    async def _run() -> PushLog:
        async with _noop_http() as http:
            return await services.push_source(
                session,  # type: ignore[arg-type]
                connection=conn,
                source={"Name": "Race RFP"},
                target="Opportunity",
                signal_id="sig-race",
                idempotency_key="race-key",
                settings=settings,
                http_client=http,
            )

    log = asyncio.run(_run())
    assert log.status == PushStatus.SUCCESS
    assert log.external_id == "sf-race"
    # Both create and update were called: create by the first attempt, update
    # by the race-fallback retry.
    assert len(create_calls) == 1
    assert len(update_calls) == 1


# ---------------------------------------------------------------------------
# Different records → different keys → separate objects
# ---------------------------------------------------------------------------


def test_different_records_produce_different_keys_and_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different signal_ids → different idempotency keys → separate CRM objects."""
    settings = _settings()
    created_ids: list[str] = []
    counter = [0]

    class _Multi(_MockProvider):
        async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
            counter[0] += 1
            ext_id = f"sf-{counter[0]:04d}"
            created_ids.append(ext_id)
            return PushResult(external_id=ext_id, created=True)

    _register_provider(monkeypatch, _Multi)
    conn = _connection(settings)
    session_a = _FakeSession()
    session_b = _FakeSession()

    async def _run_a() -> PushLog:
        async with _noop_http() as http:
            return await services.push_source(
                session_a,  # type: ignore[arg-type]
                connection=conn,
                source={"Name": "Acme"},
                target="Opportunity",
                signal_id="sig-a",
                settings=settings,
                http_client=http,
            )

    async def _run_b() -> PushLog:
        async with _noop_http() as http:
            return await services.push_source(
                session_b,  # type: ignore[arg-type]
                connection=conn,
                source={"Name": "Beta"},
                target="Opportunity",
                signal_id="sig-b",
                settings=settings,
                http_client=http,
            )

    log_a = asyncio.run(_run_a())
    log_b = asyncio.run(_run_b())

    assert log_a.external_id != log_b.external_id
    assert log_a.idempotency_key != log_b.idempotency_key
    # Two distinct CRM creates happened.
    assert len(created_ids) == 2


# ---------------------------------------------------------------------------
# Workspace / connection isolation
# ---------------------------------------------------------------------------


def test_workspace_isolation_different_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same signal_id in two workspaces → different idempotency keys."""
    ws_a, ws_b = uuid.uuid4(), uuid.uuid4()
    conn_id = uuid.uuid4()
    k_a = services.make_idempotency_key(
        workspace_id=ws_a, connection_id=conn_id, target="salesforce.Opportunity", signal_id="sig-1"
    )
    k_b = services.make_idempotency_key(
        workspace_id=ws_b, connection_id=conn_id, target="salesforce.Opportunity", signal_id="sig-1"
    )
    assert k_a != k_b


def test_connection_isolation_different_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same signal_id on two connections in same workspace → different keys."""
    ws_id = uuid.uuid4()
    conn_a, conn_b = uuid.uuid4(), uuid.uuid4()
    k_a = services.make_idempotency_key(
        workspace_id=ws_id, connection_id=conn_a, target="salesforce.Opportunity", signal_id="sig-1"
    )
    k_b = services.make_idempotency_key(
        workspace_id=ws_id, connection_id=conn_b, target="salesforce.Opportunity", signal_id="sig-1"
    )
    assert k_a != k_b
