"""Live-DB API tests for the K2 Salesforce surface (discovery, mapping, push).

Drives the field-mapping endpoints end-to-end with a mocked Salesforce REST
transport (no live org): object/field discovery, field-mapping CRUD, and a push
that creates/updates an Opportunity and records the push-log with the external
id. Also covers admin-gating + workspace isolation. Skips without a DSN
(``DATABASE_DIRECT_URL`` / ``DATABASE_URL``).
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
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import MembershipRole
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

INSTANCE = "https://acme.my.salesforce.com"
ACCOUNT = {"instance_url": INSTANCE}


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _hdr(token: str, ws_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws_id}


def _create_workspace(client: TestClient, token: str, name: str = "K2 WS") -> dict[str, Any]:
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


def _seed_connection(workspace_id: str) -> str:
    """Seed a healthy Salesforce connection with an encrypted token + instance_url."""
    new_id = uuid.uuid4()
    cipher = services.token_cipher(get_settings())

    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            conn = Connection(
                id=new_id,
                workspace_id=uuid.UUID(workspace_id),
                provider=IntegrationProviderKind.SALESFORCE,
                name="Acme prod",
                status=ConnectionStatus.HEALTHY,
                default_targets=["Opportunity"],
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
# Mock Salesforce REST transport injected via services.default_http_client
# ---------------------------------------------------------------------------


def _salesforce_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/sobjects"):
        return httpx.Response(
            200,
            json={
                "sobjects": [
                    {"name": "Opportunity", "label": "Opportunity", "createable": True},
                    {
                        "name": "Civic_Signal__c",
                        "label": "Civic Signal",
                        "createable": True,
                        "custom": True,
                    },
                    {"name": "ReadOnly", "label": "RO", "createable": False},
                ]
            },
        )
    if path.endswith("/describe"):
        return httpx.Response(
            200,
            json={
                "fields": [
                    {
                        "name": "Name",
                        "label": "Name",
                        "type": "string",
                        "createable": True,
                        "updateable": True,
                        "nillable": False,
                    },
                    {
                        "name": "Amount",
                        "label": "Amount",
                        "type": "currency",
                        "createable": True,
                        "updateable": True,
                        "nillable": True,
                    },
                ]
            },
        )
    if request.method == "POST":
        return httpx.Response(201, json={"id": "0061T00000ABCDE", "success": True})
    if request.method == "PATCH":
        return httpx.Response(204)
    return httpx.Response(404, json=[{"message": "not found"}])


@pytest.fixture
def salesforce_http(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Mock the Salesforce REST transport (a fresh client per service call)."""
    monkeypatch.setattr(
        services,
        "default_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_salesforce_handler)),
    )
    yield


# ---------------------------------------------------------------------------
# Object / field discovery
# ---------------------------------------------------------------------------


def test_discover_objects(client: TestClient, salesforce_http: None) -> None:
    token, _ = _signup(client, "k2-disc@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    resp = client.get(f"{CONNECTIONS}/{conn_id}/discover/objects", headers=_hdr(token, ws["id"]))
    assert resp.status_code == 200, resp.text
    names = {o["name"] for o in resp.json()["data"]}
    assert names == {"Opportunity", "Civic_Signal__c"}


def test_discover_fields(client: TestClient, salesforce_http: None) -> None:
    token, _ = _signup(client, "k2-fields@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    resp = client.get(
        f"{CONNECTIONS}/{conn_id}/discover/fields",
        params={"object": "Opportunity"},
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "Opportunity"
    names = {f["name"] for f in body["data"]}
    assert names == {"Name", "Amount"}


def test_discover_requires_admin(client: TestClient, salesforce_http: None) -> None:
    owner_token, _ = _signup(client, "k2-disc-owner@example.com")
    ws = _create_workspace(client, owner_token, "Gate WS")
    conn_id = _seed_connection(ws["id"])
    member_token, member_id = _signup(client, "k2-disc-member@example.com")
    _add_member_at_role(member_id, ws["id"], MembershipRole.MEMBER)
    resp = client.get(
        f"{CONNECTIONS}/{conn_id}/discover/objects", headers=_hdr(member_token, ws["id"])
    )
    assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# Field-mapping CRUD
# ---------------------------------------------------------------------------


def test_field_mapping_crud(client: TestClient) -> None:
    token, _ = _signup(client, "k2-map@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    base = f"{CONNECTIONS}/{conn_id}/field-mappings"

    # Initially empty.
    assert client.get(base, headers=_hdr(token, ws["id"])).json() == {"data": []}

    # Upsert a mapping.
    saved = client.put(
        base,
        json={
            "target_object": "Opportunity",
            "field_map": {"Name": "signal.title", "Amount": "signal.fields.amount_cents"},
            "constants": {"StageName": "Prospecting"},
        },
        headers=_hdr(token, ws["id"]),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["target_object"] == "Opportunity"
    assert saved.json()["field_map"]["Name"] == "signal.title"

    # It now lists.
    listing = client.get(base, headers=_hdr(token, ws["id"])).json()
    assert len(listing["data"]) == 1

    # Re-save the same target updates (no duplicate).
    again = client.put(
        base,
        json={"target_object": "Opportunity", "field_map": {"Name": "signal.summary"}},
        headers=_hdr(token, ws["id"]),
    )
    assert again.status_code == 200
    assert client.get(base, headers=_hdr(token, ws["id"])).json()["data"][0]["field_map"] == {
        "Name": "signal.summary"
    }
    assert len(client.get(base, headers=_hdr(token, ws["id"])).json()["data"]) == 1

    # Delete.
    deleted = client.delete(f"{base}/Opportunity", headers=_hdr(token, ws["id"]))
    assert deleted.status_code == 204
    assert client.get(base, headers=_hdr(token, ws["id"])).json() == {"data": []}


def test_field_mapping_requires_admin(client: TestClient) -> None:
    owner_token, _ = _signup(client, "k2-map-owner@example.com")
    ws = _create_workspace(client, owner_token, "Map Gate")
    conn_id = _seed_connection(ws["id"])
    member_token, member_id = _signup(client, "k2-map-member@example.com")
    _add_member_at_role(member_id, ws["id"], MembershipRole.MEMBER)
    resp = client.put(
        f"{CONNECTIONS}/{conn_id}/field-mappings",
        json={"target_object": "Opportunity", "field_map": {}},
        headers=_hdr(member_token, ws["id"]),
    )
    assert resp.status_code == 403


def test_field_mapping_workspace_isolated(client: TestClient) -> None:
    token_a, _ = _signup(client, "k2-map-a@example.com")
    token_b, _ = _signup(client, "k2-map-b@example.com")
    ws_a = _create_workspace(client, token_a, "Map A")
    ws_b = _create_workspace(client, token_b, "Map B")
    conn_a = _seed_connection(ws_a["id"])
    # Workspace B cannot read/write workspace A's connection mappings (404 — the
    # connection is resolved scoped to the active workspace).
    resp = client.get(f"{CONNECTIONS}/{conn_a}/field-mappings", headers=_hdr(token_b, ws_b["id"]))
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Push: create / update Opportunity, records push-log + external id
# ---------------------------------------------------------------------------


def test_push_creates_opportunity_and_records_log(
    client: TestClient, salesforce_http: None
) -> None:
    token, _ = _signup(client, "k2-push@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    # Save a mapping so the push shapes a body from the source.
    client.put(
        f"{CONNECTIONS}/{conn_id}/field-mappings",
        json={
            "target_object": "Opportunity",
            "field_map": {"Name": "signal.title"},
            "constants": {"StageName": "Prospecting"},
        },
        headers=_hdr(token, ws["id"]),
    )

    resp = client.post(
        f"{CONNECTIONS}/{conn_id}/push",
        json={
            "source": {"signal": {"title": "Northshore RFP"}},
            "target": "Opportunity",
            "signal_id": "sig-1",
            "idempotency_key": "idem-1",
        },
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 200, resp.text
    log = resp.json()["push_log"]
    assert log["status"] == "success"
    assert log["external_id"] == "0061T00000ABCDE"
    assert log["target"] == "salesforce.Opportunity"
    assert log["signal_id"] == "sig-1"

    # The push-log lists it.
    pl = client.get(PUSH_LOG, headers=_hdr(token, ws["id"])).json()
    assert pl["data"][0]["external_id"] == "0061T00000ABCDE"


def test_push_repush_updates_via_idempotency_key(client: TestClient, salesforce_http: None) -> None:
    token, _ = _signup(client, "k2-repush@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])
    body = {
        "source": {"signal": {"title": "RFP"}},
        "target": "Opportunity",
        "field_map_override": {"Name": "signal.title"},
        "idempotency_key": "stable-key",
    }
    first = client.post(f"{CONNECTIONS}/{conn_id}/push", json=body, headers=_hdr(token, ws["id"]))
    assert first.status_code == 200, first.text
    assert first.json()["push_log"]["external_id"] == "0061T00000ABCDE"

    # Re-push with the same idempotency key reuses the external id (update path).
    second = client.post(f"{CONNECTIONS}/{conn_id}/push", json=body, headers=_hdr(token, ws["id"]))
    assert second.status_code == 200, second.text
    assert second.json()["push_log"]["status"] == "success"
    assert second.json()["push_log"]["external_id"] == "0061T00000ABCDE"


def test_push_validation_failure_records_typed_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token, _ = _signup(client, "k2-push-fail@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"])

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json=[
                {
                    "errorCode": "REQUIRED_FIELD_MISSING",
                    "message": "Required fields are missing: [CloseDate]",
                    "fields": ["CloseDate"],
                }
            ],
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
            "field_map_override": {"Name": "signal.title"},
        },
        headers=_hdr(token, ws["id"]),
    )

    assert resp.status_code == 200, resp.text
    log = resp.json()["push_log"]
    # Validation errors are non-retryable → dead-letter with the typed error.
    assert log["status"] == "dead_letter"
    assert log["error"]["code"] == "validation"
    assert "CloseDate" in log["error"]["message"]


def test_push_requires_admin(client: TestClient, salesforce_http: None) -> None:
    owner_token, _ = _signup(client, "k2-push-owner@example.com")
    ws = _create_workspace(client, owner_token, "Push Gate")
    conn_id = _seed_connection(ws["id"])
    member_token, member_id = _signup(client, "k2-push-member@example.com")
    _add_member_at_role(member_id, ws["id"], MembershipRole.MEMBER)
    resp = client.post(
        f"{CONNECTIONS}/{conn_id}/push",
        json={"source": {}, "target": "Opportunity"},
        headers=_hdr(member_token, ws["id"]),
    )
    assert resp.status_code == 403


def test_push_unknown_connection_404(client: TestClient, salesforce_http: None) -> None:
    token, _ = _signup(client, "k2-push-404@example.com")
    ws = _create_workspace(client, token)
    resp = client.post(
        f"{CONNECTIONS}/{uuid.uuid4()}/push",
        json={"source": {}, "target": "Opportunity"},
        headers=_hdr(token, ws["id"]),
    )
    assert resp.status_code == 404
