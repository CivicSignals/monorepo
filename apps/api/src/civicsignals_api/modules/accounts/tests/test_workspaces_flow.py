"""End-to-end workspace flow tests (B5): create, list, get, switch, scoping.

These need Postgres (CITEXT + UUID columns); the ``client`` fixture skips when no
DSN is configured. CI provides one.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"

PASSWORD = "s3cure-pa55word"


def _signup(client: TestClient, email: str) -> str:
    """Register a user via the auth signup endpoint and return its access token."""
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tokens"]["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_workspace_makes_caller_owner(client: TestClient) -> None:
    token = _signup(client, "owner@example.com")
    resp = client.post(WORKSPACES, json={"name": "Acme SLED"}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Acme SLED"
    assert body["slug"] == "acme-sled"
    assert body["role"] == "owner"
    assert body["owner_id"]
    assert resp.headers["location"].endswith(f"/workspaces/{body['id']}")


def test_create_workspace_requires_auth(client: TestClient) -> None:
    resp = client.post(WORKSPACES, json={"name": "No Auth"})
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_slug_collision_gets_a_unique_suffix(client: TestClient) -> None:
    token = _signup(client, "dup@example.com")
    first = client.post(WORKSPACES, json={"name": "Same Name"}, headers=_auth(token))
    second = client.post(WORKSPACES, json={"name": "Same Name"}, headers=_auth(token))
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["slug"] == "same-name"
    assert second.json()["slug"] != first.json()["slug"]
    assert second.json()["slug"].startswith("same-name-")


def test_explicit_duplicate_slug_conflicts(client: TestClient) -> None:
    token = _signup(client, "explicit@example.com")
    first = client.post(
        WORKSPACES, json={"name": "Alpha", "slug": "shared-slug"}, headers=_auth(token)
    )
    assert first.status_code == 201
    dup = client.post(
        WORKSPACES, json={"name": "Beta", "slug": "shared-slug"}, headers=_auth(token)
    )
    assert dup.status_code == 409
    assert dup.json()["type"].endswith("/slug_taken")


def test_list_returns_only_my_workspaces(client: TestClient) -> None:
    alice = _signup(client, "alice@example.com")
    bob = _signup(client, "bob@example.com")
    client.post(WORKSPACES, json={"name": "Alice WS"}, headers=_auth(alice))
    client.post(WORKSPACES, json={"name": "Bob WS"}, headers=_auth(bob))

    alice_list = client.get(WORKSPACES, headers=_auth(alice))
    assert alice_list.status_code == 200
    names = [w["name"] for w in alice_list.json()["items"]]
    assert names == ["Alice WS"]
    assert alice_list.json()["next_cursor"] is None


def test_list_is_cursor_paginated(client: TestClient) -> None:
    token = _signup(client, "many@example.com")
    for i in range(3):
        client.post(WORKSPACES, json={"name": f"WS {i}"}, headers=_auth(token))

    page1 = client.get(f"{WORKSPACES}?limit=2", headers=_auth(token))
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None

    page2 = client.get(f"{WORKSPACES}?limit=2&cursor={body1['next_cursor']}", headers=_auth(token))
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None
    ids = {w["id"] for w in body1["items"]} | {w["id"] for w in body2["items"]}
    assert len(ids) == 3  # no overlap across pages


def test_get_workspace_as_member(client: TestClient) -> None:
    token = _signup(client, "member@example.com")
    created = client.post(WORKSPACES, json={"name": "Mine"}, headers=_auth(token)).json()
    resp = client.get(f"{WORKSPACES}/{created['id']}", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]
    assert resp.json()["role"] == "owner"


def test_get_workspace_non_member_is_404_not_403(client: TestClient) -> None:
    owner = _signup(client, "ws-owner@example.com")
    outsider = _signup(client, "outsider@example.com")
    created = client.post(WORKSPACES, json={"name": "Private"}, headers=_auth(owner)).json()

    resp = client.get(f"{WORKSPACES}/{created['id']}", headers=_auth(outsider))
    # 404, not 403 — existence is not leaked across tenants (doc 08 §1.7).
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("/not_found")


def test_switch_sets_last_active_and_enforces_membership(client: TestClient) -> None:
    owner = _signup(client, "switcher@example.com")
    outsider = _signup(client, "intruder@example.com")
    created = client.post(WORKSPACES, json={"name": "Switchy"}, headers=_auth(owner)).json()

    # A member can switch.
    ok = client.post(f"{WORKSPACES}/{created['id']}/switch", headers=_auth(owner))
    assert ok.status_code == 200
    assert ok.json()["id"] == created["id"]

    # A non-member cannot (404).
    denied = client.post(f"{WORKSPACES}/{created['id']}/switch", headers=_auth(outsider))
    assert denied.status_code == 404


def test_get_unknown_workspace_is_404(client: TestClient) -> None:
    token = _signup(client, "ghost@example.com")
    resp = client.get(f"{WORKSPACES}/00000000-0000-7000-8000-000000000000", headers=_auth(token))
    assert resp.status_code == 404


def test_invalid_cursor_is_400(client: TestClient) -> None:
    token = _signup(client, "badcursor@example.com")
    resp = client.get(f"{WORKSPACES}?cursor=not-a-cursor", headers=_auth(token))
    assert resp.status_code == 400
