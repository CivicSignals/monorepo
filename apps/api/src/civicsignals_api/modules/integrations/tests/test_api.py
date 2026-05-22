"""Live-DB API tests for the integrations module (K1).

Connection CRUD + workspace isolation + admin-gating, the OAuth flow (start →
callback stores encrypted tokens), and the push-log read API. Skip without a DSN
(``DATABASE_DIRECT_URL`` / ``DATABASE_URL``).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.config import get_settings
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import MembershipRole
from civicsignals_api.modules.integrations import providers, services
from civicsignals_api.modules.integrations.models import (
    Connection,
    ConnectionStatus,
    IntegrationProviderKind,
    PushStatus,
)
from civicsignals_api.modules.integrations.providers import (
    IntegrationProvider,
    OAuth2AuthorizationCodeMixin,
    OAuthConfig,
    PushRequest,
    PushResult,
)
from civicsignals_api.modules.integrations.tests.conftest import _require_db

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
CONNECTIONS = "/api/v1/integrations/connections"
PUSH_LOG = "/api/v1/integrations/push-log"
CALLBACK = "/api/v1/integrations/oauth/callback"
PASSWORD = "s3cur3-P4ssword!"


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _hdr(token: str, ws_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws_id}


def _create_workspace(client: TestClient, token: str, name: str = "K1 WS") -> dict[str, Any]:
    resp = client.post(WORKSPACES, json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _add_member_at_role(user_id: str, workspace_id: str, role: MembershipRole) -> None:
    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            await accounts_services.add_member(
                session,
                workspace_id=uuid.UUID(workspace_id),
                user_id=uuid.UUID(user_id),
                role=role,
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())


# ---------------------------------------------------------------------------
# Test provider: a configured OAuth provider with a mocked token endpoint
# ---------------------------------------------------------------------------


class _TestProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
    kind = IntegrationProviderKind.SALESFORCE

    def oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            authorize_url="https://provider.test/authorize",
            token_url="https://provider.test/token",
            scopes=("read", "write"),
            client_id="cid",
            client_secret="csecret",
        )

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        return PushResult(external_id="ext-1")


def _token_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "access-AAA",
            "refresh_token": "refresh-BBB",
            "expires_in": 3600,
            "scope": "read write",
        },
    )


@pytest.fixture
def configured_provider(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Register a configured test provider + mock the OAuth token transport.

    ``default_http_client`` is patched to mint a *fresh* mock client per call
    (the services close the client they own after each use), and the Salesforce
    client creds are injected via env so ``get_settings`` reports the provider as
    configured. The ``lru_cache`` is cleared on entry and exit so neither this
    test nor its neighbours see a stale ``Settings``.
    """
    monkeypatch.setitem(providers.REGISTRY, IntegrationProviderKind.SALESFORCE, _TestProvider)
    monkeypatch.setattr(
        services,
        "default_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_token_handler)),
    )
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "cid")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "csecret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Connection CRUD + auth/admin gating + workspace isolation
# ---------------------------------------------------------------------------


def test_list_connections_requires_auth(client: TestClient) -> None:
    resp = client.get(CONNECTIONS)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_list_connections_empty_for_owner(client: TestClient) -> None:
    token, _ = _signup(client, "k1-owner@example.com")
    ws = _create_workspace(client, token)
    resp = client.get(CONNECTIONS, headers=_hdr(token, ws["id"]))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"data": []}


def test_create_connection_requires_admin(client: TestClient) -> None:
    owner_token, _ = _signup(client, "k1-adminowner@example.com")
    ws = _create_workspace(client, owner_token, "Gate WS")
    member_token, member_id = _signup(client, "k1-member@example.com")
    _add_member_at_role(member_id, ws["id"], MembershipRole.MEMBER)

    resp = client.post(
        CONNECTIONS,
        json={"provider": "salesforce", "name": "X"},
        headers=_hdr(member_token, ws["id"]),
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["type"].endswith("/forbidden")


def test_create_connection_unconfigured_provider_returns_422(client: TestClient) -> None:
    """With no client creds wired up, salesforce connect is a clean 422."""
    get_settings.cache_clear()
    token, _ = _signup(client, "k1-unconfigured@example.com")
    ws = _create_workspace(client, token)
    resp = client.post(
        CONNECTIONS,
        json={"provider": "salesforce", "name": "Acme"},
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"].endswith("/provider_not_configured")
    # Nothing should have been persisted.
    listing = client.get(CONNECTIONS, headers=_hdr(token, ws["id"]))
    assert listing.json() == {"data": []}


def test_create_connection_webhook_unregistered_returns_422(client: TestClient) -> None:
    """K1 registers no webhook provider yet (L3) → 422 provider_unavailable."""
    token, _ = _signup(client, "k1-webhook@example.com")
    ws = _create_workspace(client, token)
    resp = client.post(
        CONNECTIONS,
        json={"provider": "webhook", "name": "My webhook"},
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["type"].endswith("/provider_unavailable")


def test_create_connection_starts_oauth(client: TestClient, configured_provider: None) -> None:
    token, _ = _signup(client, "k1-oauthstart@example.com")
    ws = _create_workspace(client, token)
    resp = client.post(
        CONNECTIONS,
        json={"provider": "salesforce", "name": "Acme prod", "default_targets": ["opportunity"]},
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending_oauth"
    assert body["redirect_url"].startswith("https://provider.test/authorize?")
    assert "state=" in body["redirect_url"]
    # The connection now shows up in the list as pending.
    listing = client.get(CONNECTIONS, headers=_hdr(token, ws["id"])).json()
    assert len(listing["data"]) == 1
    assert listing["data"][0]["status"] == "pending_oauth"
    assert listing["data"][0]["default_targets"] == ["opportunity"]


def test_delete_connection(client: TestClient) -> None:
    token, _ = _signup(client, "k1-delete@example.com")
    ws = _create_workspace(client, token)
    # Seed a connection directly (DB), then delete via API.
    conn_id = _seed_connection(ws["id"], status=ConnectionStatus.HEALTHY)
    resp = client.delete(f"{CONNECTIONS}/{conn_id}", headers=_hdr(token, ws["id"]))
    assert resp.status_code == 204
    assert client.get(CONNECTIONS, headers=_hdr(token, ws["id"])).json() == {"data": []}


def test_delete_connection_other_workspace_404(client: TestClient) -> None:
    token_a, _ = _signup(client, "k1-iso-a@example.com")
    token_b, _ = _signup(client, "k1-iso-b@example.com")
    ws_a = _create_workspace(client, token_a, "Iso A")
    ws_b = _create_workspace(client, token_b, "Iso B")
    conn_id = _seed_connection(ws_a["id"], status=ConnectionStatus.HEALTHY)
    # Workspace B admin cannot delete workspace A's connection.
    resp = client.delete(f"{CONNECTIONS}/{conn_id}", headers=_hdr(token_b, ws_b["id"]))
    assert resp.status_code == 404


def test_connections_workspace_isolated(client: TestClient) -> None:
    token_a, _ = _signup(client, "k1-list-a@example.com")
    token_b, _ = _signup(client, "k1-list-b@example.com")
    ws_a = _create_workspace(client, token_a, "List A")
    ws_b = _create_workspace(client, token_b, "List B")
    _seed_connection(ws_a["id"], status=ConnectionStatus.HEALTHY)

    list_a = client.get(CONNECTIONS, headers=_hdr(token_a, ws_a["id"])).json()
    list_b = client.get(CONNECTIONS, headers=_hdr(token_b, ws_b["id"])).json()
    assert len(list_a["data"]) == 1
    assert list_b["data"] == []


# ---------------------------------------------------------------------------
# OAuth callback: stores encrypted tokens, transitions to healthy
# ---------------------------------------------------------------------------


def test_oauth_callback_stores_tokens(client: TestClient, configured_provider: None) -> None:
    token, _ = _signup(client, "k1-callback@example.com")
    ws = _create_workspace(client, token)
    create = client.post(
        CONNECTIONS,
        json={"provider": "salesforce", "name": "Acme prod"},
        headers=_hdr(token, ws["id"]),
    )
    assert create.status_code == 201, create.text
    redirect_url = create.json()["redirect_url"]
    state = httpx.URL(redirect_url).params["state"]
    connection_id = create.json()["id"]

    # The provider redirects the browser back with code + state. We do NOT
    # follow the 302 (it points at the web app).
    cb = client.get(
        CALLBACK, params={"code": "the-auth-code", "state": state}, follow_redirects=False
    )
    assert cb.status_code == 302
    assert "integration=connected" in cb.headers["location"]

    # The connection is now healthy with stored (encrypted) tokens + scopes.
    row = _load_connection(connection_id)
    assert row.status == ConnectionStatus.HEALTHY
    assert row.access_token_encrypted is not None
    assert row.refresh_token_encrypted is not None
    # Stored ciphertext is NOT the plaintext token.
    assert "access-AAA" not in (row.access_token_encrypted or "")
    cipher = services.token_cipher(get_settings())
    assert cipher.decrypt(row.access_token_encrypted or "") == "access-AAA"
    assert set(row.scopes) == {"read", "write"}


def test_oauth_callback_rejects_bad_state(client: TestClient, configured_provider: None) -> None:
    cb = client.get(
        CALLBACK,
        params={"code": "x", "state": "forged.deadbeef"},
        follow_redirects=False,
    )
    assert cb.status_code == 400
    assert cb.json()["type"].endswith("/invalid_oauth_state")


def test_oauth_callback_consent_denied_redirects_error(
    client: TestClient, configured_provider: None
) -> None:
    token, _ = _signup(client, "k1-deny@example.com")
    ws = _create_workspace(client, token)
    create = client.post(
        CONNECTIONS,
        json={"provider": "salesforce", "name": "Acme"},
        headers=_hdr(token, ws["id"]),
    )
    state = httpx.URL(create.json()["redirect_url"]).params["state"]
    cb = client.get(
        CALLBACK, params={"error": "access_denied", "state": state}, follow_redirects=False
    )
    assert cb.status_code == 302
    assert "integration=error" in cb.headers["location"]


# ---------------------------------------------------------------------------
# Push-log read API
# ---------------------------------------------------------------------------


def test_push_log_requires_admin(client: TestClient) -> None:
    owner_token, _ = _signup(client, "k1-pl-owner@example.com")
    ws = _create_workspace(client, owner_token, "PL WS")
    viewer_token, viewer_id = _signup(client, "k1-pl-viewer@example.com")
    _add_member_at_role(viewer_id, ws["id"], MembershipRole.VIEWER)
    resp = client.get(PUSH_LOG, headers=_hdr(viewer_token, ws["id"]))
    assert resp.status_code == 403


def test_push_log_lists_and_is_workspace_scoped(client: TestClient) -> None:
    token_a, _ = _signup(client, "k1-pl-a@example.com")
    token_b, _ = _signup(client, "k1-pl-b@example.com")
    ws_a = _create_workspace(client, token_a, "PL A")
    ws_b = _create_workspace(client, token_b, "PL B")
    conn_id = _seed_connection(ws_a["id"], status=ConnectionStatus.HEALTHY)
    _seed_push_log(ws_a["id"], conn_id, status=PushStatus.SUCCESS, external_id="ext-9")

    log_a = client.get(PUSH_LOG, headers=_hdr(token_a, ws_a["id"]))
    assert log_a.status_code == 200, log_a.text
    body_a = log_a.json()
    assert len(body_a["data"]) == 1
    assert body_a["data"][0]["external_id"] == "ext-9"
    assert body_a["data"][0]["status"] == "success"
    assert "next_cursor" in body_a

    # Workspace B does not see workspace A's push-log.
    log_b = client.get(PUSH_LOG, headers=_hdr(token_b, ws_b["id"]))
    assert log_b.json()["data"] == []


def test_push_log_failed_entry_surfaces_typed_error(client: TestClient) -> None:
    token, _ = _signup(client, "k1-pl-err@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"], status=ConnectionStatus.DEGRADED)
    _seed_push_log(
        ws["id"],
        conn_id,
        status=PushStatus.FAILED,
        error_code="validation",
        error_message="CloseDate is required",
    )
    body = client.get(PUSH_LOG, headers=_hdr(token, ws["id"])).json()
    assert body["data"][0]["status"] == "failed"
    assert body["data"][0]["error"]["code"] == "validation"
    assert body["data"][0]["error"]["message"] == "CloseDate is required"


# ---------------------------------------------------------------------------
# DB seed helpers (write directly with a NullPool engine, loop-agnostic)
# ---------------------------------------------------------------------------


def _seed_connection(workspace_id: str, *, status: ConnectionStatus) -> str:
    new_id = uuid.uuid4()

    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            conn = Connection(
                id=new_id,
                workspace_id=uuid.UUID(workspace_id),
                provider=IntegrationProviderKind.SALESFORCE,
                name="Seeded",
                status=status,
            )
            session.add(conn)
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())
    return str(new_id)


def _seed_push_log(
    workspace_id: str,
    connection_id: str,
    *,
    status: PushStatus,
    external_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    from civicsignals_api.modules.integrations.models import PushErrorCode, PushLog

    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            log = PushLog(
                workspace_id=uuid.UUID(workspace_id),
                connection_id=uuid.UUID(connection_id),
                target="salesforce.opportunity",
                status=status,
                external_id=external_id,
                error_code=PushErrorCode(error_code) if error_code else None,
                error_message=error_message,
                attempt_count=1 if status != PushStatus.PENDING else 0,
            )
            session.add(log)
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())


def _load_connection(connection_id: str) -> Connection:
    async def _do() -> Connection:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            row = await services.get_connection_unscoped(session, uuid.UUID(connection_id))
            assert row is not None
            return row
        await engine.dispose()  # pragma: no cover

    return asyncio.run(_do())
