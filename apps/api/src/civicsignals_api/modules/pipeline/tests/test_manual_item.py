"""J4 — Manual pipeline item creation tests.

Covers:
- Creating a manual item (no signal_id) succeeds and appears in item list.
- Records a ``created`` activity entry automatically.
- Stage-belongs-to-workspace validation (404 when stage is in another workspace).
- Workspace isolation (items in workspace A invisible in workspace B).
- RequireMember gating (viewer role is rejected with 403).
- Signal-linked creation path still works via the existing POST /items endpoint.
- Manual item has ``signal_id = null`` in the response.
- Default-stage fallback when ``stage_id`` is omitted.

All tests require a live Postgres DSN (skipped without one). The ``client``
fixture in ``conftest.py`` owns DB setup.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import MembershipRole

_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
STAGES = "/api/v1/pipeline/stages"
ITEMS = "/api/v1/pipeline/items"
MANUAL = "/api/v1/pipeline/items/manual"
PASSWORD = "s3cure-pa55w0rd!"

# ---- helpers ------------------------------------------------------------------


def _signup(client: TestClient, email: str) -> str:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tokens"]["access_token"])


def _create_workspace(client: TestClient, token: str, name: str = "Test WS") -> str:
    resp = client.post(
        WORKSPACES, json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _auth(token: str, workspace_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace_id}


def _get_stages(client: TestClient, token: str, ws: str) -> list[dict[str, object]]:
    resp = client.get(STAGES, headers=_auth(token, ws))
    assert resp.status_code == 200, resp.text
    return list(resp.json()["items"])


def _activity(client: TestClient, token: str, ws: str, item_id: str) -> list[dict[str, object]]:
    resp = client.get(f"{ITEMS}/{item_id}/activity", headers=_auth(token, ws))
    assert resp.status_code == 200, resp.text
    return list(resp.json()["items"])


# ---- J4: manual item creation ------------------------------------------------


def test_create_manual_item_no_signal_succeeds(client: TestClient) -> None:
    """POST /pipeline/items/manual creates an item with signal_id=None."""
    token = _signup(client, "j4-basic@example.com")
    ws = _create_workspace(client, token)

    resp = client.post(
        MANUAL,
        json={"title": "Sourced manually at conference"},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Sourced manually at conference"
    assert body["signal_id"] is None  # no signal — manual item
    assert body["status"] == "active"
    assert body["stage_id"]  # placed in default stage


def test_create_manual_item_with_all_fields(client: TestClient) -> None:
    """Manual item with all optional fields is created correctly."""
    token = _signup(client, "j4-allfields@example.com")
    ws = _create_workspace(client, token)
    stages = _get_stages(client, token, ws)
    stage_id = stages[2]["id"]  # "Contacted"

    fake_owner = "01900000-0000-7000-8000-000000000099"
    resp = client.post(
        MANUAL,
        json={
            "title": "City Hall outreach",
            "stage_id": stage_id,
            "notes": "Met at city council meeting.",
            "value_estimate": "75000.00",
            "owner_id": fake_owner,
        },
        headers=_auth(token, ws),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "City Hall outreach"
    assert body["stage_id"] == stage_id
    assert body["notes"] == "Met at city council meeting."
    assert body["value_estimate"] == "75000.00"
    assert body["owner_id"] == fake_owner
    assert body["signal_id"] is None


def test_create_manual_item_appears_in_list(client: TestClient) -> None:
    """A manually created item is returned by GET /pipeline/items."""
    token = _signup(client, "j4-list@example.com")
    ws = _create_workspace(client, token)

    resp = client.post(
        MANUAL, json={"title": "Manual lead"}, headers=_auth(token, ws)
    )
    assert resp.status_code == 201, resp.text
    item_id = resp.json()["id"]

    # Item appears in the list
    list_resp = client.get(ITEMS, headers=_auth(token, ws))
    assert list_resp.status_code == 200
    ids = [i["id"] for i in list_resp.json()["items"]]
    assert item_id in ids


def test_create_manual_item_records_created_activity(client: TestClient) -> None:
    """Creating a manual item automatically records a 'created' activity (J3)."""
    token = _signup(client, "j4-activity@example.com")
    ws = _create_workspace(client, token)

    resp = client.post(
        MANUAL, json={"title": "Activity-tracked lead"}, headers=_auth(token, ws)
    )
    assert resp.status_code == 201, resp.text
    item_id = resp.json()["id"]

    activities = _activity(client, token, ws, item_id)
    assert len(activities) >= 1
    first = activities[0]
    assert first["activity_type"] == "created"
    assert first["item_id"] == item_id
    assert first["workspace_id"] == ws


def test_create_manual_item_uses_default_stage(client: TestClient) -> None:
    """When stage_id is omitted, the item lands in the workspace's default stage."""
    token = _signup(client, "j4-default-stage@example.com")
    ws = _create_workspace(client, token)

    # Get the default stage
    stages = _get_stages(client, token, ws)
    default_stage = next((s for s in stages if s["is_default"]), stages[0])

    resp = client.post(
        MANUAL, json={"title": "Default stage lead"}, headers=_auth(token, ws)
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["stage_id"] == default_stage["id"]


def test_create_manual_item_stage_not_in_workspace_is_404(client: TestClient) -> None:
    """Stage from another workspace causes 404 (workspace scope enforced)."""
    alice = _signup(client, "j4-scope-alice@example.com")
    bob = _signup(client, "j4-scope-bob@example.com")
    ws_a = _create_workspace(client, alice, "Alice WS")
    ws_b = _create_workspace(client, bob, "Bob WS")

    # Get a stage id from Bob's workspace
    bob_stages = _get_stages(client, bob, ws_b)
    bob_stage_id = bob_stages[0]["id"]

    # Alice tries to create an item in Bob's stage (cross-workspace)
    resp = client.post(
        MANUAL,
        json={"title": "Cross-ws lead", "stage_id": bob_stage_id},
        headers=_auth(alice, ws_a),
    )
    assert resp.status_code == 404, resp.text


def test_create_manual_item_workspace_isolation(client: TestClient) -> None:
    """Manual item in workspace A is invisible from workspace B."""
    alice = _signup(client, "j4-iso-alice@example.com")
    bob = _signup(client, "j4-iso-bob@example.com")
    ws_a = _create_workspace(client, alice, "Alice Items WS")
    ws_b = _create_workspace(client, bob, "Bob Items WS")

    # Alice creates a manual item
    alice_resp = client.post(
        MANUAL, json={"title": "Alice-only lead"}, headers=_auth(alice, ws_a)
    )
    assert alice_resp.status_code == 201
    item_id = alice_resp.json()["id"]

    # Bob cannot see Alice's item (404)
    bob_get = client.get(f"{ITEMS}/{item_id}", headers=_auth(bob, ws_b))
    assert bob_get.status_code == 404

    # Bob's item list is empty
    bob_list = client.get(ITEMS, headers=_auth(bob, ws_b))
    assert bob_list.status_code == 200
    assert all(i["id"] != item_id for i in bob_list.json()["items"])


def test_create_manual_item_location_header(client: TestClient) -> None:
    """Response includes Location header pointing to the new item."""
    token = _signup(client, "j4-location@example.com")
    ws = _create_workspace(client, token)

    resp = client.post(
        MANUAL, json={"title": "Locatable lead"}, headers=_auth(token, ws)
    )
    assert resp.status_code == 201, resp.text
    item_id = resp.json()["id"]
    assert resp.headers.get("location", "").endswith(f"/pipeline/items/{item_id}")


def test_create_manual_item_title_required(client: TestClient) -> None:
    """Missing title returns 422 (Pydantic validation)."""
    token = _signup(client, "j4-no-title@example.com")
    ws = _create_workspace(client, token)

    resp = client.post(MANUAL, json={}, headers=_auth(token, ws))
    assert resp.status_code == 422


def test_create_manual_item_requires_auth(client: TestClient) -> None:
    """Unauthenticated request returns 401."""
    resp = client.post(MANUAL, json={"title": "Anon lead"})
    assert resp.status_code == 401


def test_create_manual_item_requires_workspace_header(client: TestClient) -> None:
    """Missing X-Workspace-Id header returns 400."""
    token = _signup(client, "j4-no-ws@example.com")
    resp = client.post(
        MANUAL,
        json={"title": "No WS lead"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


# ---- RequireMember gating (B7) -----------------------------------------------


def _add_member_at_role(user_id: str, workspace_id: str, role: MembershipRole) -> None:
    """Insert a membership at ``role`` directly via the service layer (no invite needed)."""

    async def _do() -> None:
        assert _DSN is not None
        engine = create_async_engine(_DSN, poolclass=NullPool)
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


def _signup_with_id(client: TestClient, email: str) -> tuple[str, str]:
    """Register a user, return (access_token, user_id)."""
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def test_create_manual_item_viewer_role_is_403(client: TestClient) -> None:
    """A workspace member with viewer role cannot create a manual item (403)."""
    # Owner who controls the workspace
    owner, _ = _signup_with_id(client, "j4-rbac-owner@example.com")
    ws = _create_workspace(client, owner, "RBAC Test WS")

    # Register a viewer and add them directly (bypassing the invite email flow)
    viewer_token, viewer_id = _signup_with_id(client, "j4-rbac-viewer@example.com")
    _add_member_at_role(viewer_id, ws, MembershipRole.VIEWER)

    # Viewer attempts to create a manual item — must be 403
    resp = client.post(
        MANUAL,
        json={"title": "Viewer should be blocked"},
        headers=_auth(viewer_token, ws),
    )
    assert resp.status_code == 403, resp.text


# ---- Signal-linked path still works ------------------------------------------


def test_signal_linked_item_creation_still_works(client: TestClient) -> None:
    """The existing POST /pipeline/items endpoint still accepts signal_id (backward compat)."""
    token = _signup(client, "j4-signal-compat@example.com")
    ws = _create_workspace(client, token)
    fake_signal = "01900000-0000-7000-8000-000000000001"

    resp = client.post(
        ITEMS,
        json={"title": "Signal-linked item", "signal_id": fake_signal},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["signal_id"] == fake_signal
    assert body["signal_id"] is not None


def test_signal_linked_and_manual_items_coexist(client: TestClient) -> None:
    """Manual and signal-linked items can coexist in the same workspace."""
    token = _signup(client, "j4-coexist@example.com")
    ws = _create_workspace(client, token)
    fake_signal = "01900000-0000-7000-8000-000000000002"

    # Create one of each
    sig_item = client.post(
        ITEMS,
        json={"title": "Signal item", "signal_id": fake_signal},
        headers=_auth(token, ws),
    ).json()
    manual_item = client.post(
        MANUAL,
        json={"title": "Manual item"},
        headers=_auth(token, ws),
    ).json()

    # Both appear in the list
    listed = client.get(ITEMS, headers=_auth(token, ws)).json()["items"]
    ids = {i["id"] for i in listed}
    assert sig_item["id"] in ids
    assert manual_item["id"] in ids

    # Signal-linked has signal_id; manual has None
    sig_in_list = next(i for i in listed if i["id"] == sig_item["id"])
    manual_in_list = next(i for i in listed if i["id"] == manual_item["id"])
    assert sig_in_list["signal_id"] == fake_signal
    assert manual_in_list["signal_id"] is None
