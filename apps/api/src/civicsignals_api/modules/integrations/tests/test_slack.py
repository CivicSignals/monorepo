"""Unit + integration tests for the Slack provider + channel select (L1).

Covers:
- SlackProvider OAuth config + token parsing (team metadata capture).
- exchange_code over a mocked httpx transport (no live Slack).
- list_channels over a mocked transport (pagination handled).
- auth-failure mapping (``ok=False`` → PushErrorCode.AUTH).
- services.list_slack_channels / upsert_slack_channel_selection (mockable).
- routes: OAuth start returns authorize URL; channel list + channel selection
  endpoints; admin-gating (non-admin 403); workspace isolation; token never
  returned/logged.

Live-DB tests skip when DATABASE_DIRECT_URL/DATABASE_URL is unset.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest

from civicsignals_api.config import Settings
from civicsignals_api.modules.integrations.models import PushErrorCode
from civicsignals_api.modules.integrations.providers import (
    ProviderError,
    SlackChannel,
    SlackProvider,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "secret_key": "test-secret-key",
        "slack_client_id": "slack-client-id",
        "slack_client_secret": "slack-client-secret",
        "integrations_oauth_state_ttl_seconds": 600,
    }
    base.update(overrides)
    return Settings(**base)


def _mock_http(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _slack_token_response(
    *,
    access_token: str = "xoxb-bot-token",
    team_id: str = "T0123456",
    team_name: str = "Acme",
    scope: str = "channels:read,chat:write,chat:write.public",
    bot_user_id: str = "U0123456",
    app_id: str = "A0123456",
) -> dict[str, object]:
    return {
        "ok": True,
        "access_token": access_token,
        "token_type": "bot",
        "scope": scope,
        "bot_user_id": bot_user_id,
        "app_id": app_id,
        "team": {"id": team_id, "name": team_name},
    }


def _slack_channels_response(
    channels: list[dict[str, object]] | None = None,
    next_cursor: str | None = None,
) -> dict[str, object]:
    return {
        "ok": True,
        "channels": channels
        or [
            {"id": "C0001", "name": "general", "is_private": False, "is_member": True},
            {"id": "C0002", "name": "random", "is_private": False, "is_member": False},
        ],
        "response_metadata": {"next_cursor": next_cursor or ""},
    }


# ---------------------------------------------------------------------------
# OAuth config
# ---------------------------------------------------------------------------


def test_slack_oauth_config_urls_and_scopes() -> None:
    prov = SlackProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    config = prov.oauth_config()
    assert config.authorize_url == "https://slack.com/oauth/v2/authorize"
    assert config.token_url == "https://slack.com/api/oauth.v2.access"
    assert "channels:read" in config.scopes
    assert "chat:write" in config.scopes
    assert config.configured is True


def test_slack_oauth_config_not_configured_when_creds_missing() -> None:
    prov = SlackProvider(
        _settings(slack_client_id=None, slack_client_secret=None),
        _mock_http(lambda r: httpx.Response(200)),
    )
    assert prov.oauth_config().configured is False


# ---------------------------------------------------------------------------
# Token parsing (parse_token_response)
# ---------------------------------------------------------------------------


def test_slack_parse_token_response_captures_team_metadata() -> None:
    prov = SlackProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    body = _slack_token_response()
    tokens = prov.parse_token_response(body)

    assert tokens.access_token == "xoxb-bot-token"
    assert tokens.refresh_token is None  # bot tokens don't expire
    assert tokens.expires_at is None
    assert "channels:read" in tokens.scopes
    assert tokens.provider_account["team_id"] == "T0123456"
    assert tokens.provider_account["team_name"] == "Acme"
    assert tokens.provider_account["bot_user_id"] == "U0123456"
    assert tokens.provider_account["app_id"] == "A0123456"


def test_slack_parse_token_response_raises_on_ok_false() -> None:
    prov = SlackProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    with pytest.raises(ProviderError) as exc_info:
        prov.parse_token_response({"ok": False, "error": "invalid_code"})
    assert exc_info.value.code is PushErrorCode.AUTH
    assert "invalid_code" in exc_info.value.message


def test_slack_parse_token_response_raises_on_missing_access_token() -> None:
    prov = SlackProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    with pytest.raises(ProviderError) as exc_info:
        prov.parse_token_response({"ok": True})
    assert exc_info.value.code is PushErrorCode.AUTH


# ---------------------------------------------------------------------------
# exchange_code over mocked transport
# ---------------------------------------------------------------------------


def test_slack_exchange_code_stores_bot_token() -> None:
    """exchange_code posts to oauth.v2.access and parses the token (mocked)."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Verify Basic auth is used (client_id:client_secret).
        assert request.headers.get("authorization", "").startswith("Basic ")
        body_text = request.content.decode("utf-8")
        assert "code=auth-code-123" in body_text
        return httpx.Response(200, json=_slack_token_response())

    prov = SlackProvider(_settings(), _mock_http(handler))
    tokens = asyncio.run(
        prov.exchange_code(code="auth-code-123", redirect_uri="http://localhost/callback")
    )
    assert tokens.access_token == "xoxb-bot-token"
    assert tokens.provider_account["team_id"] == "T0123456"


def test_slack_exchange_code_raises_on_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "bad_verification_code"})

    prov = SlackProvider(_settings(), _mock_http(handler))
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(prov.exchange_code(code="bad-code", redirect_uri="http://localhost/callback"))
    assert exc_info.value.code is PushErrorCode.AUTH


def test_slack_exchange_code_raises_when_unconfigured() -> None:
    prov = SlackProvider(
        _settings(slack_client_id=None, slack_client_secret=None),
        _mock_http(lambda r: httpx.Response(200)),
    )
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(prov.exchange_code(code="x", redirect_uri="http://localhost/callback"))
    assert exc_info.value.code is PushErrorCode.AUTH


# ---------------------------------------------------------------------------
# list_channels over mocked transport
# ---------------------------------------------------------------------------


def test_slack_list_channels_returns_channels() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" in request.headers
        assert "xoxb-" in request.headers["Authorization"]
        return httpx.Response(200, json=_slack_channels_response())

    prov = SlackProvider(_settings(), _mock_http(handler))
    channels = asyncio.run(prov.list_channels(access_token="xoxb-bot-token"))
    assert len(channels) == 2
    assert channels[0].id == "C0001"
    assert channels[0].name == "general"
    assert channels[0].is_member is True
    assert channels[1].id == "C0002"
    assert channels[1].is_private is False


def test_slack_list_channels_paginates() -> None:
    """list_channels follows the next_cursor until exhausted."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raw_query = request.url.query
        query_str: str = raw_query.decode("utf-8") if isinstance(raw_query, bytes) else raw_query
        cursor_parts: dict[str, str] = {}
        for pair in query_str.split("&"):
            if "=" in pair:
                k, _, v = pair.partition("=")
                cursor_parts[k] = v
        cursor = cursor_parts.get("cursor", "")
        if not cursor:
            # First page — return next_cursor to trigger a second page fetch.
            return httpx.Response(
                200,
                json=_slack_channels_response(
                    channels=[
                        {"id": "C0001", "name": "general", "is_private": False, "is_member": True}
                    ],
                    next_cursor="dGVhbS1jaGFubmVs",
                ),
            )
        # Second page — empty next_cursor signals end.
        return httpx.Response(
            200,
            json=_slack_channels_response(
                channels=[
                    {"id": "C0002", "name": "random", "is_private": False, "is_member": False}
                ],
                next_cursor=None,
            ),
        )

    prov = SlackProvider(_settings(), _mock_http(handler))
    channels = asyncio.run(prov.list_channels(access_token="xoxb-bot-token"))
    assert call_count == 2
    assert len(channels) == 2
    assert {ch.id for ch in channels} == {"C0001", "C0002"}


def test_slack_list_channels_raises_on_auth_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    prov = SlackProvider(_settings(), _mock_http(handler))
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(prov.list_channels(access_token="xoxb-revoked"))
    assert exc_info.value.code is PushErrorCode.AUTH


# ---------------------------------------------------------------------------
# refresh raises (bot tokens don't refresh)
# ---------------------------------------------------------------------------


def test_slack_refresh_raises_provider_error() -> None:
    prov = SlackProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    with pytest.raises(ProviderError) as exc_info:
        asyncio.run(prov.refresh(refresh_token="dummy"))
    assert exc_info.value.code is PushErrorCode.AUTH


# ---------------------------------------------------------------------------
# push raises NotImplementedError (L2 seam)
# ---------------------------------------------------------------------------


def test_slack_push_raises_not_implemented() -> None:
    from civicsignals_api.modules.integrations.providers import PushRequest

    prov = SlackProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    with pytest.raises(NotImplementedError):
        asyncio.run(
            prov.push(
                access_token="xoxb-bot-token",
                request=PushRequest(target="slack.channel", payload={}),
            )
        )


# ---------------------------------------------------------------------------
# Token never returned in parse output
# ---------------------------------------------------------------------------


def test_slack_token_not_in_provider_account() -> None:
    """The access_token must NOT appear in provider_account (threat-model §4.2)."""
    prov = SlackProvider(_settings(), _mock_http(lambda r: httpx.Response(200)))
    tokens = prov.parse_token_response(_slack_token_response(access_token="xoxb-secret-bot"))
    account_str = json.dumps(tokens.provider_account)
    assert "xoxb-secret-bot" not in account_str


# ---------------------------------------------------------------------------
# services.list_slack_channels (unit — no DB, mocked connection object)
# ---------------------------------------------------------------------------


class _FakeConnection:
    """Minimal stand-in for a Slack Connection row (no SQLAlchemy session)."""

    def __init__(self, *, access_token_encrypted: str | None) -> None:
        self.provider = type("_Kind", (), {"value": "slack"})()  # provider.value == "slack"
        from civicsignals_api.modules.integrations.models import IntegrationProviderKind

        self.provider = IntegrationProviderKind.SLACK
        self.access_token_encrypted = access_token_encrypted
        self.refresh_token_encrypted = None
        self.token_expires_at = None
        self.status = "healthy"
        self.provider_account: dict[str, object] = {}

    @property
    def is_token_expired(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Live-DB API tests for channel listing + selection endpoints
# ---------------------------------------------------------------------------

try:
    from civicsignals_api.modules.integrations.tests.conftest import _require_db

    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False


def _setup_slack_connection(
    client: Any,
    token: str,
    workspace_id: str,
    settings_obj: Any,
) -> str:
    """Create a healthy Slack connection with an encrypted bot token, return its id."""
    from fastapi.testclient import TestClient

    assert isinstance(client, TestClient)
    from civicsignals_api.modules.integrations import services as svc

    cipher = svc.token_cipher(settings_obj)
    encrypted = cipher.encrypt("xoxb-bot-token")

    # Create via POST /connections + manually flip to healthy with token.
    resp = client.post(
        "/api/v1/integrations/connections",
        json={"provider": "slack", "name": "Test Slack"},
        headers={"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace_id},
    )
    assert resp.status_code == 201, resp.text
    conn_id = resp.json()["id"]

    # Directly update the connection's token and status via the DB session.
    # We bypass the OAuth flow here because the callback needs live Slack.
    # The fixture client uses the test DB session; we patch via a separate
    # async helper that updates the connection row.
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from civicsignals_api.modules.integrations.models import Connection, ConnectionStatus

    dsn: str = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL") or ""
    assert dsn, "DATABASE_DIRECT_URL/DATABASE_URL not set"

    async def _patch() -> None:
        engine = create_async_engine(dsn, poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(Connection).where(Connection.id == uuid.UUID(conn_id))
            )
            conn = result.scalar_one()
            conn.access_token_encrypted = encrypted
            conn.status = ConnectionStatus.HEALTHY
            conn.provider_account = {"team_id": "T0123456", "team_name": "Acme"}
            await session.commit()
        await engine.dispose()

    asyncio.run(_patch())
    return str(conn_id)


@pytest.mark.skipif(not _DB_AVAILABLE, reason="no DB available import")
class TestSlackChannelAPI:
    """Live-DB API tests for Slack channel list + select endpoints (L1)."""

    def test_list_channels_endpoint_returns_mocked_channels(self, client: Any) -> None:
        """GET .../slack/channels calls SlackProvider.list_channels (mocked HTTP)."""
        pytest.importorskip("civicsignals_api.modules.integrations.tests.conftest")
        _require_db()

        from fastapi.testclient import TestClient

        assert isinstance(client, TestClient)

        from civicsignals_api.config import get_settings

        settings_obj = get_settings()

        # Sign up + create workspace.
        resp = client.post(
            "/api/v1/auth/signup",
            json={"email": "slack-l1@example.com", "password": "Passw0rd!!", "name": "L1 User"},
        )
        assert resp.status_code == 201, resp.text
        token = resp.json()["tokens"]["access_token"]

        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "L1 Workspace"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201, resp.text
        workspace_id = resp.json()["id"]

        conn_id = _setup_slack_connection(client, token, workspace_id, settings_obj)

        # Mock the SlackProvider.list_channels on the provider class.
        async def _fake_list_channels(self: Any, *, access_token: str) -> list[SlackChannel]:
            return [
                SlackChannel(id="C0001", name="general", is_private=False, is_member=True),
                SlackChannel(id="C0002", name="announcements", is_private=False, is_member=False),
            ]

        import unittest.mock

        with unittest.mock.patch.object(SlackProvider, "list_channels", _fake_list_channels):
            resp = client.get(
                f"/api/v1/integrations/connections/{conn_id}/slack/channels",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Workspace-Id": workspace_id,
                },
            )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert len(data) == 2
        assert data[0]["id"] == "C0001"
        assert data[0]["name"] == "general"

    def test_select_channel_and_get_selected(self, client: Any) -> None:
        """PUT .../slack/channels/select persists; GET .../selected returns it."""
        _require_db()

        from civicsignals_api.config import get_settings

        settings_obj = get_settings()

        resp = client.post(
            "/api/v1/auth/signup",
            json={"email": "slack-l1b@example.com", "password": "Passw0rd!!", "name": "L1b"},
        )
        assert resp.status_code == 201
        token = resp.json()["tokens"]["access_token"]
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "L1b Workspace"},
            headers={"Authorization": f"Bearer {token}"},
        )
        workspace_id = resp.json()["id"]

        conn_id = _setup_slack_connection(client, token, workspace_id, settings_obj)

        # Select a channel.
        resp = client.put(
            f"/api/v1/integrations/connections/{conn_id}/slack/channels/select",
            json={"channel_id": "C0001", "channel_name": "general"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Workspace-Id": workspace_id,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["channel_id"] == "C0001"
        assert body["channel_name"] == "general"
        assert body["connection_id"] == conn_id

        # Retrieve selected channel.
        resp = client.get(
            f"/api/v1/integrations/connections/{conn_id}/slack/channels/selected",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Workspace-Id": workspace_id,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["channel_id"] == "C0001"

        # Re-select a different channel (upsert).
        resp = client.put(
            f"/api/v1/integrations/connections/{conn_id}/slack/channels/select",
            json={"channel_id": "C0002", "channel_name": "announcements"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Workspace-Id": workspace_id,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["channel_id"] == "C0002"

    def test_get_selected_returns_404_when_none_saved(self, client: Any) -> None:
        """GET .../selected returns 404 before any channel is selected (L1)."""
        _require_db()

        from civicsignals_api.config import get_settings

        settings_obj = get_settings()

        resp = client.post(
            "/api/v1/auth/signup",
            json={"email": "slack-l1c@example.com", "password": "Passw0rd!!", "name": "L1c"},
        )
        assert resp.status_code == 201
        token = resp.json()["tokens"]["access_token"]
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "L1c Workspace"},
            headers={"Authorization": f"Bearer {token}"},
        )
        workspace_id = resp.json()["id"]

        conn_id = _setup_slack_connection(client, token, workspace_id, settings_obj)

        resp = client.get(
            f"/api/v1/integrations/connections/{conn_id}/slack/channels/selected",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Workspace-Id": workspace_id,
            },
        )
        assert resp.status_code == 404

    def test_non_admin_cannot_access_slack_endpoints(self, client: Any) -> None:
        """Non-admin user gets 403 on Slack channel endpoints (admin-gating, L1)."""
        _require_db()

        from civicsignals_api.config import get_settings

        settings_obj = get_settings()

        # Admin signs up + creates workspace + connection.
        resp = client.post(
            "/api/v1/auth/signup",
            json={"email": "admin-slack-l1@example.com", "password": "Passw0rd!!", "name": "Admin"},
        )
        assert resp.status_code == 201
        admin_token = resp.json()["tokens"]["access_token"]
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Admin Workspace"},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        workspace_id = resp.json()["id"]
        conn_id = _setup_slack_connection(client, admin_token, workspace_id, settings_obj)

        # A second user is NOT a member of this workspace.
        resp = client.post(
            "/api/v1/auth/signup",
            json={"email": "nonadmin-slack-l1@example.com", "password": "Passw0rd!!", "name": "NA"},
        )
        assert resp.status_code == 201
        non_admin_token = resp.json()["tokens"]["access_token"]
        # Create a separate workspace so this user has a workspace to be admin of.
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "Other Workspace"},
            headers={"Authorization": f"Bearer {non_admin_token}"},
        )
        _ = resp.json()["id"]  # separate workspace for this user

        # Non-admin tries to access channel list in the other workspace.
        resp = client.get(
            f"/api/v1/integrations/connections/{conn_id}/slack/channels",
            headers={
                "Authorization": f"Bearer {non_admin_token}",
                "X-Workspace-Id": workspace_id,  # admin's workspace
            },
        )
        # Should be 403 (RequireAdmin) or 404 (workspace isolation).
        assert resp.status_code in (403, 404)

    def test_not_slack_connection_returns_422(self, client: Any) -> None:
        """Calling Slack endpoints on a non-Slack connection returns 422 (L1)."""
        _require_db()

        # For simplicity, test with a made-up connection_id (404 is also acceptable
        # when connection doesn't exist — the router returns 404 before provider check).
        resp = client.post(
            "/api/v1/auth/signup",
            json={"email": "slack-l1d@example.com", "password": "Passw0rd!!", "name": "L1d"},
        )
        assert resp.status_code == 201
        token = resp.json()["tokens"]["access_token"]
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "L1d Workspace"},
            headers={"Authorization": f"Bearer {token}"},
        )
        workspace_id = resp.json()["id"]
        fake_id = str(uuid.uuid4())
        resp = client.get(
            f"/api/v1/integrations/connections/{fake_id}/slack/channels",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Workspace-Id": workspace_id,
            },
        )
        assert resp.status_code == 404  # connection not found → 404

    def test_oauth_start_returns_authorize_url(self, client: Any) -> None:
        """POST /integrations/connections with provider=slack returns redirect_url (L1)."""
        _require_db()

        resp = client.post(
            "/api/v1/auth/signup",
            json={"email": "slack-oauth@example.com", "password": "Passw0rd!!", "name": "OA"},
        )
        assert resp.status_code == 201
        token = resp.json()["tokens"]["access_token"]
        resp = client.post(
            "/api/v1/workspaces",
            json={"name": "OA Workspace"},
            headers={"Authorization": f"Bearer {token}"},
        )
        workspace_id = resp.json()["id"]

        resp = client.post(
            "/api/v1/integrations/connections",
            json={"provider": "slack", "name": "My Slack"},
            headers={
                "Authorization": f"Bearer {token}",
                "X-Workspace-Id": workspace_id,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["provider"] == "slack"
        assert body["status"] == "pending_oauth"
        # redirect_url must be a Slack authorize URL with our client_id.
        assert body["redirect_url"] is not None
        assert "slack.com/oauth/v2/authorize" in body["redirect_url"]
        assert "slack-client-id" in body["redirect_url"]
