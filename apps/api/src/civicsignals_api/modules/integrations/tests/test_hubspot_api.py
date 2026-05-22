"""Live-DB API tests for the K3 HubSpot surface (discovery, push, idempotency).

Drives the provider-generic field-mapping endpoints end-to-end against a HubSpot
connection with a mocked CRM v3 REST transport (no live portal): object/property
discovery, a deal push that records the push-log with the external id, custom
property push, and an idempotent re-push that updates (PATCH) instead of
duplicating. Skips without a DSN (``DATABASE_DIRECT_URL`` / ``DATABASE_URL``).

Mirrors ``test_salesforce_api.py`` — the K3 provider plugs into the same K1/K2/K4
framework, so the only HubSpot-specific surface is the mocked transport.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.config import get_settings
from civicsignals_api.modules.integrations import services
from civicsignals_api.modules.integrations.models import (
    Connection,
    ConnectionStatus,
    IntegrationProviderKind,
)
from civicsignals_api.modules.integrations.tests.conftest import _require_db

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
CONNECTIONS = "/api/v1/integrations/connections"
PUSH_LOG = "/api/v1/integrations/push-log"
PASSWORD = "s3cur3-P4ssword!"

ACCOUNT = {"hub_id": "12345678"}


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _hdr(token: str, ws_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws_id}


def _create_workspace(client: TestClient, token: str, name: str = "K3 WS") -> dict[str, Any]:
    resp = client.post(WORKSPACES, json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _seed_connection(workspace_id: str) -> str:
    """Seed a healthy HubSpot connection with an encrypted token + hub_id."""
    new_id = uuid.uuid4()
    cipher = services.token_cipher(get_settings())

    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            conn = Connection(
                id=new_id,
                workspace_id=uuid.UUID(workspace_id),
                provider=IntegrationProviderKind.HUBSPOT,
                name="Acme HubSpot",
                status=ConnectionStatus.HEALTHY,
                default_targets=["deals"],
                provider_account=dict(ACCOUNT),
            )
            conn.access_token_encrypted = cipher.encrypt("live-access-token")
            conn.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
            session.add(conn)
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())
    return str(new_id)


# ---------------------------------------------------------------------------
# Mock HubSpot CRM v3 transport injected via services.default_http_client
# ---------------------------------------------------------------------------


def _hubspot_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/crm/v3/schemas":
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "civic_signal",
                        "fullyQualifiedName": "p12345_civic_signal",
                        "labels": {"plural": "Civic Signals"},
                    }
                ]
            },
        )
    if path.startswith("/crm/v3/properties/"):
        return httpx.Response(
            200,
            json={
                "results": [
                    {"name": "dealname", "label": "Deal Name", "type": "string"},
                    {"name": "amount", "label": "Amount", "type": "number"},
                ]
            },
        )
    if request.method == "POST":
        return httpx.Response(201, json={"id": "8675309", "properties": {}})
    if request.method == "PATCH":
        # Echo the id from the path on update.
        rid = path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"id": rid, "properties": {}})
    return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def hubspot_http(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Mock the HubSpot REST transport (a fresh client per service call)."""
    monkeypatch.setattr(
        services,
        "default_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_hubspot_handler)),
    )
    yield


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_objects(client: TestClient, hubspot_http: None) -> None:
    token, _ = _signup(client, "k3-disc@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    resp = client.get(f"{CONNECTIONS}/{conn_id}/discover/objects", headers=_hdr(token, ws["id"]))
    assert resp.status_code == 200, resp.text
    names = {o["name"] for o in resp.json()["data"]}
    assert "deals" in names
    assert "p12345_civic_signal" in names


def test_discover_fields(client: TestClient, hubspot_http: None) -> None:
    token, _ = _signup(client, "k3-fields@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    resp = client.get(
        f"{CONNECTIONS}/{conn_id}/discover/fields",
        params={"object": "deals"},
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "deals"
    names = {f["name"] for f in body["data"]}
    assert names == {"dealname", "amount"}


# ---------------------------------------------------------------------------
# Push: create Deal + custom property, records push-log + external id
# ---------------------------------------------------------------------------


def test_push_creates_deal_and_records_log(client: TestClient, hubspot_http: None) -> None:
    token, _ = _signup(client, "k3-push@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    # Map signal fields onto a standard property + a custom property.
    client.put(
        f"{CONNECTIONS}/{conn_id}/field-mappings",
        json={
            "target_object": "deals",
            "field_map": {
                "dealname": "signal.title",
                "civic_signal_url": "signal.url",  # custom property
            },
            "constants": {"dealstage": "appointmentscheduled"},
        },
        headers=_hdr(token, ws["id"]),
    )

    resp = client.post(
        f"{CONNECTIONS}/{conn_id}/push",
        json={
            "source": {"signal": {"title": "Northshore RFP", "url": "https://x"}},
            "target": "deals",
            "signal_id": "sig-1",
            "idempotency_key": "idem-1",
        },
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 200, resp.text
    log = resp.json()["push_log"]
    assert log["status"] == "success"
    assert log["external_id"] == "8675309"
    assert log["target"] == "hubspot.deals"
    assert log["signal_id"] == "sig-1"
    # The custom property is shaped into the request body.
    assert log["request"]["civic_signal_url"] == "https://x"
    assert log["request"]["dealstage"] == "appointmentscheduled"

    pl = client.get(PUSH_LOG, headers=_hdr(token, ws["id"])).json()
    assert pl["data"][0]["external_id"] == "8675309"


def test_push_repush_updates_via_idempotency_key(client: TestClient, hubspot_http: None) -> None:
    token, _ = _signup(client, "k3-repush@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    body = {
        "source": {"signal": {"title": "RFP"}},
        "target": "deals",
        "field_map_override": {"dealname": "signal.title"},
        "idempotency_key": "stable-key",
    }
    first = client.post(f"{CONNECTIONS}/{conn_id}/push", json=body, headers=_hdr(token, ws["id"]))
    assert first.status_code == 200, first.text
    assert first.json()["push_log"]["external_id"] == "8675309"

    # Re-push with the same idempotency key reuses the external id (PATCH update,
    # not a duplicate POST create) — the mock PATCH echoes the id from the path.
    second = client.post(f"{CONNECTIONS}/{conn_id}/push", json=body, headers=_hdr(token, ws["id"]))
    assert second.status_code == 200, second.text
    assert second.json()["push_log"]["status"] == "success"
    assert second.json()["push_log"]["external_id"] == "8675309"


def test_push_validation_failure_records_typed_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, _ = _signup(client, "k3-push-fail@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "message": "Property values were not valid: dealstage is required",
                "category": "VALIDATION_ERROR",
            },
        )

    monkeypatch.setattr(
        services,
        "default_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(failing_handler)),
    )
    resp = client.post(
        f"{CONNECTIONS}/{conn_id}/push",
        json={
            "source": {"signal": {"title": "X"}},
            "field_map_override": {"dealname": "signal.title"},
        },
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 200, resp.text
    log = resp.json()["push_log"]
    # Validation errors are non-retryable → dead-letter with the typed error.
    assert log["status"] == "dead_letter"
    assert log["error"]["code"] == "validation"
    assert "dealstage" in log["error"]["message"]
