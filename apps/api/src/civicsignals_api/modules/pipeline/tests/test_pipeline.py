"""End-to-end pipeline tests (J1): stages CRUD + reorder, items CRUD + move,
workspace isolation, cursor pagination.

All tests require a live Postgres DSN (skipped without one). The ``client``
fixture in ``conftest.py`` owns DB setup.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
STAGES = "/api/v1/pipeline/stages"
ITEMS = "/api/v1/pipeline/items"
PASSWORD = "s3cure-pa55w0rd!"

# ---- helpers ---------------------------------------------------------------


def _signup(client: TestClient, email: str) -> str:
    """Register a user and return its access token."""
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tokens"]["access_token"])


def _create_workspace(client: TestClient, token: str, name: str = "Test WS") -> str:
    """Create a workspace and return its id."""
    resp = client.post(
        WORKSPACES, json={"name": name}, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _auth(token: str, workspace_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Workspace-Id": workspace_id}


# ---- stage tests -----------------------------------------------------------


def test_list_stages_auto_provisions_defaults(client: TestClient) -> None:
    """First list call for a new workspace seeds the nine default stages."""
    token = _signup(client, "stage-list@example.com")
    ws = _create_workspace(client, token)
    resp = client.get(STAGES, headers=_auth(token, ws))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [s["name"] for s in body["items"]]
    assert "Saved" in names
    assert "Won" in names
    assert len(body["items"]) == 9  # nine default stages


def test_stages_ordered_by_position(client: TestClient) -> None:
    token = _signup(client, "stage-order@example.com")
    ws = _create_workspace(client, token)
    resp = client.get(STAGES, headers=_auth(token, ws))
    positions = [s["position"] for s in resp.json()["items"]]
    assert positions == sorted(positions)


def test_create_stage(client: TestClient) -> None:
    token = _signup(client, "stage-create@example.com")
    ws = _create_workspace(client, token)
    resp = client.post(STAGES, json={"name": "Follow-up"}, headers=_auth(token, ws))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Follow-up"
    assert resp.headers["location"].endswith(f"/pipeline/stages/{body['id']}")


def test_create_stage_duplicate_name_is_409(client: TestClient) -> None:
    token = _signup(client, "stage-dup@example.com")
    ws = _create_workspace(client, token)
    # Trigger provisioning first
    client.get(STAGES, headers=_auth(token, ws))
    resp = client.post(STAGES, json={"name": "Saved"}, headers=_auth(token, ws))
    assert resp.status_code == 409, resp.text


def test_get_stage(client: TestClient) -> None:
    token = _signup(client, "stage-get@example.com")
    ws = _create_workspace(client, token)
    listed = client.get(STAGES, headers=_auth(token, ws)).json()
    stage_id = listed["items"][0]["id"]
    resp = client.get(f"{STAGES}/{stage_id}", headers=_auth(token, ws))
    assert resp.status_code == 200
    assert resp.json()["id"] == stage_id


def test_get_stage_not_found(client: TestClient) -> None:
    token = _signup(client, "stage-404@example.com")
    ws = _create_workspace(client, token)
    resp = client.get(f"{STAGES}/00000000-0000-7000-8000-000000000000", headers=_auth(token, ws))
    assert resp.status_code == 404


def test_update_stage_name(client: TestClient) -> None:
    token = _signup(client, "stage-update@example.com")
    ws = _create_workspace(client, token)
    listed = client.get(STAGES, headers=_auth(token, ws)).json()
    stage_id = listed["items"][0]["id"]
    resp = client.patch(f"{STAGES}/{stage_id}", json={"name": "New Name"}, headers=_auth(token, ws))
    assert resp.status_code == 200
    assert resp.json()["name"] == "New Name"


def test_delete_stage_with_no_items(client: TestClient) -> None:
    token = _signup(client, "stage-delete@example.com")
    ws = _create_workspace(client, token)
    # Create a fresh stage (no items)
    new_stage = client.post(STAGES, json={"name": "To Delete"}, headers=_auth(token, ws)).json()
    resp = client.delete(f"{STAGES}/{new_stage['id']}", headers=_auth(token, ws))
    assert resp.status_code == 204


def test_delete_stage_with_items_is_409(client: TestClient) -> None:
    token = _signup(client, "stage-delete-item@example.com")
    ws = _create_workspace(client, token)
    listed = client.get(STAGES, headers=_auth(token, ws)).json()
    stage_id = listed["items"][0]["id"]
    # Add an item to the stage
    client.post(
        ITEMS,
        json={"title": "Blocking item", "stage_id": stage_id},
        headers=_auth(token, ws),
    )
    resp = client.delete(f"{STAGES}/{stage_id}", headers=_auth(token, ws))
    assert resp.status_code == 409


def test_reorder_stages(client: TestClient) -> None:
    token = _signup(client, "stage-reorder@example.com")
    ws = _create_workspace(client, token)
    listed = client.get(STAGES, headers=_auth(token, ws)).json()
    ids = [s["id"] for s in listed["items"]]
    reversed_ids = list(reversed(ids))
    payload = {"stages": [{"id": sid, "position": idx} for idx, sid in enumerate(reversed_ids)]}
    resp = client.put(f"{STAGES}/reorder", json=payload, headers=_auth(token, ws))
    assert resp.status_code == 200, resp.text
    new_order = [s["id"] for s in resp.json()["items"]]
    assert new_order == reversed_ids


def test_reorder_stages_bad_payload_is_400(client: TestClient) -> None:
    token = _signup(client, "stage-reorder-bad@example.com")
    ws = _create_workspace(client, token)
    client.get(STAGES, headers=_auth(token, ws))  # provision
    fake_id = "00000000-0000-7000-8000-000000000001"
    payload = {"stages": [{"id": fake_id, "position": 0}]}
    resp = client.put(f"{STAGES}/reorder", json=payload, headers=_auth(token, ws))
    assert resp.status_code == 400


def test_stages_cursor_pagination(client: TestClient) -> None:
    token = _signup(client, "stage-cursor@example.com")
    ws = _create_workspace(client, token)
    client.get(STAGES, headers=_auth(token, ws))  # provision 9 defaults
    page1 = client.get(f"{STAGES}?limit=4", headers=_auth(token, ws)).json()
    assert len(page1["items"]) == 4
    assert page1["next_cursor"] is not None
    page2 = client.get(
        f"{STAGES}?limit=4&cursor={page1['next_cursor']}", headers=_auth(token, ws)
    ).json()
    assert len(page2["items"]) == 4
    page3 = client.get(
        f"{STAGES}?limit=4&cursor={page2['next_cursor']}", headers=_auth(token, ws)
    ).json()
    assert len(page3["items"]) == 1
    assert page3["next_cursor"] is None
    all_ids = (
        [s["id"] for s in page1["items"]]
        + [s["id"] for s in page2["items"]]
        + [s["id"] for s in page3["items"]]
    )
    assert len(set(all_ids)) == 9  # no duplicates across pages


# ---- item tests ------------------------------------------------------------


def test_create_item_uses_default_stage(client: TestClient) -> None:
    token = _signup(client, "item-create@example.com")
    ws = _create_workspace(client, token)
    resp = client.post(ITEMS, json={"title": "Hot prospect"}, headers=_auth(token, ws))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Hot prospect"
    assert body["status"] == "active"
    assert body["stage_id"]  # placed in default stage


def test_create_item_with_explicit_stage(client: TestClient) -> None:
    token = _signup(client, "item-explicit@example.com")
    ws = _create_workspace(client, token)
    listed = client.get(STAGES, headers=_auth(token, ws)).json()
    stage_id = listed["items"][2]["id"]  # "Contacted"
    resp = client.post(
        ITEMS,
        json={"title": "Contacted deal", "stage_id": stage_id, "value_estimate": "250000.00"},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["stage_id"] == stage_id
    assert body["value_estimate"] == "250000.00"


def test_create_item_with_signal_id(client: TestClient) -> None:
    """signal_id is stored as a loose nullable UUID (no FK yet)."""
    token = _signup(client, "item-signal@example.com")
    ws = _create_workspace(client, token)
    fake_signal = "01900000-0000-7000-8000-000000000001"
    resp = client.post(
        ITEMS,
        json={"title": "Signal item", "signal_id": fake_signal},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["signal_id"] == fake_signal


def test_get_item(client: TestClient) -> None:
    token = _signup(client, "item-get@example.com")
    ws = _create_workspace(client, token)
    created = client.post(ITEMS, json={"title": "Get me"}, headers=_auth(token, ws)).json()
    resp = client.get(f"{ITEMS}/{created['id']}", headers=_auth(token, ws))
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_item_not_found(client: TestClient) -> None:
    token = _signup(client, "item-404@example.com")
    ws = _create_workspace(client, token)
    resp = client.get(f"{ITEMS}/00000000-0000-7000-8000-000000000000", headers=_auth(token, ws))
    assert resp.status_code == 404


def test_update_item(client: TestClient) -> None:
    token = _signup(client, "item-update@example.com")
    ws = _create_workspace(client, token)
    created = client.post(ITEMS, json={"title": "Original title"}, headers=_auth(token, ws)).json()
    resp = client.patch(
        f"{ITEMS}/{created['id']}",
        json={"title": "Updated title", "notes": "Some notes", "value_estimate": "100000.00"},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "Updated title"
    assert body["notes"] == "Some notes"
    assert body["value_estimate"] == "100000.00"


def test_update_item_status(client: TestClient) -> None:
    token = _signup(client, "item-status@example.com")
    ws = _create_workspace(client, token)
    created = client.post(ITEMS, json={"title": "Deal"}, headers=_auth(token, ws)).json()
    resp = client.patch(
        f"{ITEMS}/{created['id']}", json={"status": "won"}, headers=_auth(token, ws)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "won"


def test_move_item_between_stages(client: TestClient) -> None:
    token = _signup(client, "item-move@example.com")
    ws = _create_workspace(client, token)
    stages = client.get(STAGES, headers=_auth(token, ws)).json()["items"]
    stage_a = stages[0]["id"]
    stage_b = stages[1]["id"]

    created = client.post(
        ITEMS, json={"title": "Moving deal", "stage_id": stage_a}, headers=_auth(token, ws)
    ).json()
    assert created["stage_id"] == stage_a

    resp = client.post(
        f"{ITEMS}/{created['id']}/move",
        json={"stage_id": stage_b},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage_id"] == stage_b


def test_move_item_with_status(client: TestClient) -> None:
    token = _signup(client, "item-move-status@example.com")
    ws = _create_workspace(client, token)
    stages = client.get(STAGES, headers=_auth(token, ws)).json()["items"]
    won_stage = next(s for s in stages if s["name"] == "Won")
    created = client.post(ITEMS, json={"title": "Closing deal"}, headers=_auth(token, ws)).json()

    resp = client.post(
        f"{ITEMS}/{created['id']}/move",
        json={"stage_id": won_stage["id"], "status": "won"},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage_id"] == won_stage["id"]
    assert body["status"] == "won"


def test_move_item_to_wrong_workspace_stage_is_404(client: TestClient) -> None:
    token = _signup(client, "item-move-ws@example.com")
    ws1 = _create_workspace(client, token, "WS 1")
    ws2 = _create_workspace(client, token, "WS 2")
    # Stage from ws2
    stages_ws2 = client.get(STAGES, headers=_auth(token, ws2)).json()["items"]
    stage_ws2_id = stages_ws2[0]["id"]
    # Item in ws1
    item = client.post(ITEMS, json={"title": "Item"}, headers=_auth(token, ws1)).json()
    # Moving an item in ws1 to a stage in ws2 → stage not found in ws1 → 404
    resp = client.post(
        f"{ITEMS}/{item['id']}/move",
        json={"stage_id": stage_ws2_id},
        headers=_auth(token, ws1),
    )
    assert resp.status_code == 404


def test_assign_and_unassign_owner(client: TestClient) -> None:
    token = _signup(client, "item-assign@example.com")
    ws = _create_workspace(client, token)
    created = client.post(ITEMS, json={"title": "Assign me"}, headers=_auth(token, ws)).json()
    assert created["owner_id"] is None

    # Assign a fake member id (loose ref — validated at app layer when members land)
    member_id = "01900000-0000-7000-8000-000000000099"
    resp = client.patch(
        f"{ITEMS}/{created['id']}",
        json={"owner_id": member_id},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 200
    assert resp.json()["owner_id"] == member_id

    # Unassign: send owner_id: null — should clear the column
    resp2 = client.patch(
        f"{ITEMS}/{created['id']}",
        json={"owner_id": None},
        headers=_auth(token, ws),
    )
    assert resp2.status_code == 200
    assert resp2.json()["owner_id"] is None

    # Verify the unassignment persists
    fetched = client.get(f"{ITEMS}/{created['id']}", headers=_auth(token, ws)).json()
    assert fetched["owner_id"] is None


def test_delete_item(client: TestClient) -> None:
    token = _signup(client, "item-delete@example.com")
    ws = _create_workspace(client, token)
    created = client.post(ITEMS, json={"title": "Delete me"}, headers=_auth(token, ws)).json()
    resp = client.delete(f"{ITEMS}/{created['id']}", headers=_auth(token, ws))
    assert resp.status_code == 204
    # Verify it's gone
    assert client.get(f"{ITEMS}/{created['id']}", headers=_auth(token, ws)).status_code == 404


def test_items_cursor_pagination(client: TestClient) -> None:
    token = _signup(client, "item-cursor@example.com")
    ws = _create_workspace(client, token)
    for i in range(5):
        client.post(ITEMS, json={"title": f"Item {i}"}, headers=_auth(token, ws))

    page1 = client.get(f"{ITEMS}?limit=2", headers=_auth(token, ws)).json()
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None

    page2 = client.get(
        f"{ITEMS}?limit=2&cursor={page1['next_cursor']}", headers=_auth(token, ws)
    ).json()
    assert len(page2["items"]) == 2

    page3 = client.get(
        f"{ITEMS}?limit=2&cursor={page2['next_cursor']}", headers=_auth(token, ws)
    ).json()
    assert len(page3["items"]) == 1
    assert page3["next_cursor"] is None

    all_ids = (
        [i["id"] for i in page1["items"]]
        + [i["id"] for i in page2["items"]]
        + [i["id"] for i in page3["items"]]
    )
    assert len(set(all_ids)) == 5


def test_items_filter_by_stage(client: TestClient) -> None:
    token = _signup(client, "item-filter-stage@example.com")
    ws = _create_workspace(client, token)
    stages = client.get(STAGES, headers=_auth(token, ws)).json()["items"]
    s0, s1 = stages[0]["id"], stages[1]["id"]

    client.post(ITEMS, json={"title": "In S0", "stage_id": s0}, headers=_auth(token, ws))
    client.post(ITEMS, json={"title": "In S1", "stage_id": s1}, headers=_auth(token, ws))

    result = client.get(f"{ITEMS}?stage_id={s0}", headers=_auth(token, ws)).json()
    assert len(result["items"]) == 1
    assert result["items"][0]["stage_id"] == s0


def test_workspace_isolation_stages(client: TestClient) -> None:
    """Stages from workspace A must not be visible in workspace B."""
    alice = _signup(client, "ws-iso-alice@example.com")
    bob = _signup(client, "ws-iso-bob@example.com")
    ws_a = _create_workspace(client, alice, "Alice WS")
    ws_b = _create_workspace(client, bob, "Bob WS")

    # Alice creates a custom stage
    client.get(STAGES, headers=_auth(alice, ws_a))  # provision
    client.post(STAGES, json={"name": "Alice-Only Stage"}, headers=_auth(alice, ws_a))

    # Bob's workspace should not have Alice's stage
    bob_stages = client.get(STAGES, headers=_auth(bob, ws_b)).json()["items"]
    stage_names = [s["name"] for s in bob_stages]
    assert "Alice-Only Stage" not in stage_names


def test_workspace_isolation_items(client: TestClient) -> None:
    """Items from workspace A must not be visible in workspace B."""
    alice = _signup(client, "item-iso-alice@example.com")
    bob = _signup(client, "item-iso-bob@example.com")
    ws_a = _create_workspace(client, alice, "Alice Items WS")
    ws_b = _create_workspace(client, bob, "Bob Items WS")

    alice_item = client.post(
        ITEMS, json={"title": "Alice's Deal"}, headers=_auth(alice, ws_a)
    ).json()

    # Alice can get her item
    assert client.get(f"{ITEMS}/{alice_item['id']}", headers=_auth(alice, ws_a)).status_code == 200

    # Bob cannot see Alice's item (404, not 403)
    resp_bob = client.get(f"{ITEMS}/{alice_item['id']}", headers=_auth(bob, ws_b))
    assert resp_bob.status_code == 404


def test_items_require_workspace_header(client: TestClient) -> None:
    """Requests without a workspace are rejected."""
    token = _signup(client, "no-ws@example.com")
    resp = client.get(ITEMS, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code in (400, 404)  # no last_active_workspace yet
