"""J3 activity log tests — pipeline item activity recording and timeline.

Tests cover:
- Automatic activity recording on create_item, move_item (stage_changed),
  assign_owner (assigned), set_value (value_changed), update_item (assigned /
  value_changed).
- Comment recording via POST /pipeline/items/{id}/comments.
- GET /pipeline/items/{id}/activity — timeline read, workspace isolation,
  cursor pagination, ordering (oldest-first).
- Append-only contract — no delete/update endpoints exist for activity.

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


def _create_item(client: TestClient, token: str, ws: str, **kwargs: object) -> str:
    """Create a pipeline item and return its id string."""
    body: dict[str, object] = {"title": "Test item", **kwargs}
    resp = client.post(ITEMS, json=body, headers=_auth(token, ws))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _activity_url(item_id: str) -> str:
    return f"{ITEMS}/{item_id}/activity"


def _comment_url(item_id: str) -> str:
    return f"{ITEMS}/{item_id}/comments"


# ---- creation activity -----------------------------------------------------


def test_create_item_records_created_activity(client: TestClient) -> None:
    """Creating a pipeline item automatically records a 'created' activity."""
    token = _signup(client, "act-create@example.com")
    ws = _create_workspace(client, token)
    item_id = _create_item(client, token, ws)

    resp = client.get(_activity_url(item_id), headers=_auth(token, ws))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) >= 1
    first = body["items"][0]
    assert first["activity_type"] == "created"
    assert first["item_id"] == item_id
    assert first["workspace_id"] == ws


# ---- stage change activity -------------------------------------------------


def test_move_item_records_stage_changed_activity(client: TestClient) -> None:
    """Moving an item to a different stage records a 'stage_changed' activity."""
    token = _signup(client, "act-move@example.com")
    ws = _create_workspace(client, token)
    stages = client.get(STAGES, headers=_auth(token, ws)).json()["items"]
    stage_a: str = stages[0]["id"]
    stage_b: str = stages[1]["id"]
    stage_a_name: str = stages[0]["name"]
    stage_b_name: str = stages[1]["name"]

    item_id = _create_item(client, token, ws, stage_id=stage_a)

    resp = client.post(
        f"{ITEMS}/{item_id}/move",
        json={"stage_id": stage_b},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 200, resp.text

    act_resp = client.get(_activity_url(item_id), headers=_auth(token, ws))
    assert act_resp.status_code == 200
    activities = act_resp.json()["items"]
    # Must have at least: created + stage_changed
    types = [a["activity_type"] for a in activities]
    assert "stage_changed" in types

    stage_change = next(a for a in activities if a["activity_type"] == "stage_changed")
    assert stage_change["payload"]["from_stage_id"] == stage_a
    assert stage_change["payload"]["to_stage_id"] == stage_b
    assert stage_change["payload"]["from_stage_name"] == stage_a_name
    assert stage_change["payload"]["to_stage_name"] == stage_b_name


# ---- assign activity -------------------------------------------------------


def test_update_item_owner_records_assigned_activity(client: TestClient) -> None:
    """Changing owner_id via PATCH records an 'assigned' activity."""
    token = _signup(client, "act-assign@example.com")
    ws = _create_workspace(client, token)
    item_id = _create_item(client, token, ws)

    member_id = "01900000-0000-7000-8000-000000000099"
    client.patch(
        f"{ITEMS}/{item_id}",
        json={"owner_id": member_id},
        headers=_auth(token, ws),
    )

    act_resp = client.get(_activity_url(item_id), headers=_auth(token, ws))
    activities = act_resp.json()["items"]
    types = [a["activity_type"] for a in activities]
    assert "assigned" in types

    assigned = next(a for a in activities if a["activity_type"] == "assigned")
    assert assigned["payload"]["new_owner_id"] == member_id
    assert assigned["payload"]["old_owner_id"] is None


def test_update_item_unassign_records_assigned_activity(client: TestClient) -> None:
    """Clearing owner_id (null) via PATCH records an 'assigned' activity."""
    token = _signup(client, "act-unassign@example.com")
    ws = _create_workspace(client, token)
    member_id = "01900000-0000-7000-8000-000000000098"
    item_id = _create_item(client, token, ws)

    # First assign
    client.patch(f"{ITEMS}/{item_id}", json={"owner_id": member_id}, headers=_auth(token, ws))
    # Then unassign
    client.patch(f"{ITEMS}/{item_id}", json={"owner_id": None}, headers=_auth(token, ws))

    act_resp = client.get(_activity_url(item_id), headers=_auth(token, ws))
    activities = act_resp.json()["items"]
    assigned_entries = [a for a in activities if a["activity_type"] == "assigned"]
    # Two assigned entries: assign + unassign
    assert len(assigned_entries) == 2
    # Last one clears the owner
    last = assigned_entries[-1]
    assert last["payload"]["old_owner_id"] == member_id
    assert last["payload"]["new_owner_id"] is None


# ---- value_changed activity ------------------------------------------------


def test_update_item_value_records_value_changed_activity(client: TestClient) -> None:
    """Changing value_estimate via PATCH records a 'value_changed' activity."""
    token = _signup(client, "act-value@example.com")
    ws = _create_workspace(client, token)
    item_id = _create_item(client, token, ws)

    client.patch(
        f"{ITEMS}/{item_id}",
        json={"value_estimate": "150000.00"},
        headers=_auth(token, ws),
    )

    act_resp = client.get(_activity_url(item_id), headers=_auth(token, ws))
    activities = act_resp.json()["items"]
    types = [a["activity_type"] for a in activities]
    assert "value_changed" in types

    vc = next(a for a in activities if a["activity_type"] == "value_changed")
    assert vc["payload"]["old_value"] is None
    assert vc["payload"]["new_value"] is not None


# ---- comment ---------------------------------------------------------------


def test_add_comment_returns_201_and_activity(client: TestClient) -> None:
    """POST /comments creates a 'comment' activity entry."""
    token = _signup(client, "act-comment@example.com")
    ws = _create_workspace(client, token)
    item_id = _create_item(client, token, ws)

    resp = client.post(
        _comment_url(item_id),
        json={"text": "First comment!"},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["activity_type"] == "comment"
    assert body["payload"]["text"] == "First comment!"
    assert body["item_id"] == item_id
    assert body["workspace_id"] == ws


def test_add_comment_appears_in_timeline(client: TestClient) -> None:
    """Comment shows up when listing the item's activity timeline."""
    token = _signup(client, "act-comment-list@example.com")
    ws = _create_workspace(client, token)
    item_id = _create_item(client, token, ws)

    client.post(
        _comment_url(item_id),
        json={"text": "Hello team!"},
        headers=_auth(token, ws),
    )

    act_resp = client.get(_activity_url(item_id), headers=_auth(token, ws))
    activities = act_resp.json()["items"]
    types = [a["activity_type"] for a in activities]
    assert "comment" in types
    comment = next(a for a in activities if a["activity_type"] == "comment")
    assert comment["payload"]["text"] == "Hello team!"


def test_add_comment_to_nonexistent_item_is_404(client: TestClient) -> None:
    """Commenting on a non-existent item returns 404."""
    token = _signup(client, "act-comment-404@example.com")
    ws = _create_workspace(client, token)

    resp = client.post(
        _comment_url("00000000-0000-7000-8000-000000000000"),
        json={"text": "Ghost comment"},
        headers=_auth(token, ws),
    )
    assert resp.status_code == 404


# ---- timeline read ---------------------------------------------------------


def test_activity_timeline_single_entry_after_create(client: TestClient) -> None:
    """A freshly-created item's timeline has exactly one entry: 'created'."""
    token = _signup(client, "act-fresh@example.com")
    ws = _create_workspace(client, token)
    item_id = _create_item(client, token, ws)

    resp = client.get(_activity_url(item_id), headers=_auth(token, ws))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["activity_type"] == "created"
    assert body["next_cursor"] is None


def test_activity_ordering_oldest_first(client: TestClient) -> None:
    """Activity timeline is ordered oldest-first (ASC created_at)."""
    token = _signup(client, "act-order@example.com")
    ws = _create_workspace(client, token)
    stages = client.get(STAGES, headers=_auth(token, ws)).json()["items"]
    stage_b: str = stages[1]["id"]

    item_id = _create_item(client, token, ws)

    # Move to stage B, then comment
    client.post(f"{ITEMS}/{item_id}/move", json={"stage_id": stage_b}, headers=_auth(token, ws))
    client.post(_comment_url(item_id), json={"text": "Note"}, headers=_auth(token, ws))

    resp = client.get(_activity_url(item_id), headers=_auth(token, ws))
    activities = resp.json()["items"]
    types = [a["activity_type"] for a in activities]
    # created → stage_changed → comment (oldest first)
    assert types[0] == "created"
    assert "stage_changed" in types[1:]
    assert "comment" in types


def test_activity_timeline_workspace_isolation(client: TestClient) -> None:
    """Activity from workspace A is not visible from workspace B."""
    alice = _signup(client, "act-iso-alice@example.com")
    bob = _signup(client, "act-iso-bob@example.com")
    ws_a = _create_workspace(client, alice, "Alice WS")
    ws_b = _create_workspace(client, bob, "Bob WS")

    alice_item_id = _create_item(client, alice, ws_a)

    # Alice can see her item's activity
    resp_alice = client.get(_activity_url(alice_item_id), headers=_auth(alice, ws_a))
    assert resp_alice.status_code == 200

    # Bob cannot access Alice's item (404 — item not in Bob's workspace)
    resp_bob = client.get(_activity_url(alice_item_id), headers=_auth(bob, ws_b))
    assert resp_bob.status_code == 404


def test_activity_404_for_nonexistent_item(client: TestClient) -> None:
    """GET /activity on a non-existent item returns 404."""
    token = _signup(client, "act-get-404@example.com")
    ws = _create_workspace(client, token)

    resp = client.get(
        _activity_url("00000000-0000-7000-8000-000000000000"), headers=_auth(token, ws)
    )
    assert resp.status_code == 404


def test_activity_cursor_pagination(client: TestClient) -> None:
    """Activity timeline supports cursor pagination (oldest-first pages)."""
    token = _signup(client, "act-cursor@example.com")
    ws = _create_workspace(client, token)
    stages = client.get(STAGES, headers=_auth(token, ws)).json()["items"]
    stage_b: str = stages[1]["id"]
    stage_c: str = stages[2]["id"]

    item_id = _create_item(client, token, ws)

    # Generate 5 total activities: created + 2 moves + 2 comments
    client.post(f"{ITEMS}/{item_id}/move", json={"stage_id": stage_b}, headers=_auth(token, ws))
    client.post(f"{ITEMS}/{item_id}/move", json={"stage_id": stage_c}, headers=_auth(token, ws))
    client.post(_comment_url(item_id), json={"text": "A"}, headers=_auth(token, ws))
    client.post(_comment_url(item_id), json={"text": "B"}, headers=_auth(token, ws))

    # Page 1: 2 items
    p1 = client.get(f"{_activity_url(item_id)}?limit=2", headers=_auth(token, ws)).json()
    assert len(p1["items"]) == 2
    assert p1["next_cursor"] is not None

    # Page 2: 2 more
    p2 = client.get(
        f"{_activity_url(item_id)}?limit=2&cursor={p1['next_cursor']}",
        headers=_auth(token, ws),
    ).json()
    assert len(p2["items"]) == 2
    assert p2["next_cursor"] is not None

    # Page 3: 1 remaining
    p3 = client.get(
        f"{_activity_url(item_id)}?limit=2&cursor={p2['next_cursor']}",
        headers=_auth(token, ws),
    ).json()
    assert len(p3["items"]) == 1
    assert p3["next_cursor"] is None

    # No duplicates across pages
    all_ids = (
        [a["id"] for a in p1["items"]]
        + [a["id"] for a in p2["items"]]
        + [a["id"] for a in p3["items"]]
    )
    assert len(set(all_ids)) == 5


def test_activity_append_only_no_delete_endpoint(client: TestClient) -> None:
    """There is no DELETE endpoint for activity entries (append-only contract)."""
    token = _signup(client, "act-no-delete@example.com")
    ws = _create_workspace(client, token)
    item_id = _create_item(client, token, ws)

    activities = client.get(_activity_url(item_id), headers=_auth(token, ws)).json()["items"]
    act_id: str = activities[0]["id"]

    # Attempt to DELETE an activity entry — should 404 or 405
    resp = client.delete(f"{ITEMS}/{item_id}/activity/{act_id}", headers=_auth(token, ws))
    assert resp.status_code in (404, 405)


def test_activity_requires_workspace_header(client: TestClient) -> None:
    """Missing workspace header returns 400."""
    token = _signup(client, "act-no-ws@example.com")
    resp = client.get(
        _activity_url("00000000-0000-7000-8000-000000000000"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


def test_comment_requires_workspace_header(client: TestClient) -> None:
    """Missing workspace header on comment POST returns 400."""
    token = _signup(client, "comment-no-ws@example.com")
    resp = client.post(
        _comment_url("00000000-0000-7000-8000-000000000000"),
        json={"text": "hello"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


# ---- integration_push seam (# TODO K) -------------------------------------


def test_activity_type_integration_push_is_valid(client: TestClient) -> None:
    """The 'integration_push' activity type schema is valid (K-chain seam).

    Verifies that the ActivityType enum includes integration_push so the K-chain
    can call record_activity with it without a schema change.  This test does not
    invoke the K-chain — it only checks the enum value exists.
    """
    from civicsignals_api.modules.pipeline.models import ActivityType

    assert ActivityType.INTEGRATION_PUSH.value == "integration_push"
