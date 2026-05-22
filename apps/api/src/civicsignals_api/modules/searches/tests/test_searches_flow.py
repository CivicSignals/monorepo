"""End-to-end saved-search flow tests (H1): CRUD, workspace isolation, sharing
visibility, ownership/RBAC, cursor pagination.

These need Postgres (JSONB column); the ``client`` fixture skips when no DSN is
configured. CI provides one. A second member is inserted directly through
``accounts.services.add_member`` (no invite API in this slice, B6).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import MembershipRole as Role

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
SEARCHES = "/api/v1/searches"

PASSWORD = "s3cure-pa55word"

_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")


def _require_db() -> str:
    if not _DSN:
        pytest.skip("DATABASE_DIRECT_URL/DATABASE_URL not set; searches flow needs Postgres")
    return _DSN


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    """Return ``(access_token, user_id)`` for a fresh user."""
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scoped(token: str, workspace_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": workspace_id}


def _make_workspace(client: TestClient, token: str, name: str = "Acme SLED") -> str:
    resp = client.post(WORKSPACES, json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _add_member_at_role(user_id: str, workspace_id: str, role: Role) -> None:
    """Insert a membership at ``role`` directly (no invite API yet, B6)."""

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


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Hot RFPs",
        "filters": {
            "signal_type": "rfp_posted",
            "statuses": ["new", "pinned"],
            "min_score": 60.0,
        },
        "is_shared": False,
    }
    body.update(overrides)
    return body


# --- Create / read ----------------------------------------------------------


def test_create_saved_search(client: TestClient) -> None:
    token, user_id = _signup(client, "create@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(SEARCHES, json=_body(), headers=_scoped(token, ws))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Hot RFPs"
    assert body["workspace_id"] == ws
    assert body["created_by"] == user_id
    assert body["filters"]["signal_type"] == "rfp_posted"
    assert body["filters"]["statuses"] == ["new", "pinned"]
    assert body["is_shared"] is False
    assert resp.headers["location"].endswith(f"/searches/{body['id']}")


def test_create_with_empty_filters(client: TestClient) -> None:
    token, _ = _signup(client, "emptyfilters@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(SEARCHES, json={"name": "Everything"}, headers=_scoped(token, ws))
    assert resp.status_code == 201, resp.text
    assert resp.json()["filters"] == {}


def test_create_requires_workspace_header(client: TestClient) -> None:
    token, _ = _signup(client, "noheader@example.com")
    _make_workspace(client, token)  # exists but header absent and no last_active
    resp = client.post(SEARCHES, json=_body(), headers=_auth(token))
    assert resp.status_code == 400


def test_create_requires_auth(client: TestClient) -> None:
    resp = client.post(SEARCHES, json=_body())
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_create_rejects_unknown_signal_type(client: TestClient) -> None:
    token, _ = _signup(client, "badtype@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(
        SEARCHES,
        json=_body(filters={"signal_type": "not_a_signal"}),
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 422


def test_create_rejects_unknown_status(client: TestClient) -> None:
    token, _ = _signup(client, "badstatus@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(
        SEARCHES,
        json=_body(filters={"statuses": ["nope"]}),
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 422


def test_get_saved_search(client: TestClient) -> None:
    token, _ = _signup(client, "get@example.com")
    ws = _make_workspace(client, token)
    created = client.post(SEARCHES, json=_body(), headers=_scoped(token, ws)).json()
    resp = client.get(f"{SEARCHES}/{created['id']}", headers=_scoped(token, ws))
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_unknown_is_404(client: TestClient) -> None:
    token, _ = _signup(client, "ghost@example.com")
    ws = _make_workspace(client, token)
    resp = client.get(
        f"{SEARCHES}/00000000-0000-7000-8000-000000000000", headers=_scoped(token, ws)
    )
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("/not_found")


# --- Workspace isolation ----------------------------------------------------


def test_workspace_a_cannot_see_workspace_b_search(client: TestClient) -> None:
    owner, _ = _signup(client, "iso-owner@example.com")
    ws_a = _make_workspace(client, owner, "WS A")
    ws_b = _make_workspace(client, owner, "WS B")
    created = client.post(SEARCHES, json=_body(), headers=_scoped(owner, ws_a)).json()

    resp = client.get(f"{SEARCHES}/{created['id']}", headers=_scoped(owner, ws_b))
    assert resp.status_code == 404
    listed = client.get(SEARCHES, headers=_scoped(owner, ws_b)).json()
    assert listed["items"] == []


def test_outsider_cannot_reach_workspace_search(client: TestClient) -> None:
    owner, _ = _signup(client, "iso2-owner@example.com")
    outsider, _ = _signup(client, "iso2-outsider@example.com")
    ws = _make_workspace(client, owner)
    created = client.post(SEARCHES, json=_body(), headers=_scoped(owner, ws)).json()
    # Non-member of the workspace -> 404 (existence not leaked across tenants).
    resp = client.get(f"{SEARCHES}/{created['id']}", headers=_scoped(outsider, ws))
    assert resp.status_code == 404


# --- Sharing visibility (the H1 "share within workspace" rule) --------------


def test_private_search_invisible_to_other_member(client: TestClient) -> None:
    owner, _ = _signup(client, "share-owner@example.com")
    other, other_id = _signup(client, "share-other@example.com")
    ws = _make_workspace(client, owner)
    _add_member_at_role(other_id, ws, Role.MEMBER)

    created = client.post(
        SEARCHES, json=_body(is_shared=False), headers=_scoped(owner, ws)
    ).json()

    # The other member cannot see the owner's *private* search.
    assert client.get(f"{SEARCHES}/{created['id']}", headers=_scoped(other, ws)).status_code == 404
    assert client.get(SEARCHES, headers=_scoped(other, ws)).json()["items"] == []


def test_shared_search_visible_to_other_member(client: TestClient) -> None:
    owner, _ = _signup(client, "shared-owner@example.com")
    other, other_id = _signup(client, "shared-other@example.com")
    ws = _make_workspace(client, owner)
    _add_member_at_role(other_id, ws, Role.MEMBER)

    created = client.post(
        SEARCHES, json=_body(is_shared=True), headers=_scoped(owner, ws)
    ).json()

    # A shared search is visible (read) to another member of the workspace.
    got = client.get(f"{SEARCHES}/{created['id']}", headers=_scoped(other, ws))
    assert got.status_code == 200
    assert got.json()["id"] == created["id"]
    listed = client.get(SEARCHES, headers=_scoped(other, ws)).json()
    assert [s["id"] for s in listed["items"]] == [created["id"]]


def test_list_includes_own_and_shared_not_others_private(client: TestClient) -> None:
    owner, _ = _signup(client, "mixlist-owner@example.com")
    other, other_id = _signup(client, "mixlist-other@example.com")
    ws = _make_workspace(client, owner)
    _add_member_at_role(other_id, ws, Role.MEMBER)

    owner_shared = client.post(
        SEARCHES, json=_body(name="Owner shared", is_shared=True), headers=_scoped(owner, ws)
    ).json()
    client.post(
        SEARCHES, json=_body(name="Owner private", is_shared=False), headers=_scoped(owner, ws)
    )
    other_own = client.post(
        SEARCHES, json=_body(name="Other own", is_shared=False), headers=_scoped(other, ws)
    ).json()

    listed = client.get(SEARCHES, headers=_scoped(other, ws)).json()
    visible = {s["id"] for s in listed["items"]}
    assert visible == {owner_shared["id"], other_own["id"]}


# --- Ownership gates writes (shared != editable by non-owner) ---------------


def test_non_owner_cannot_update_shared_search(client: TestClient) -> None:
    owner, _ = _signup(client, "edit-owner@example.com")
    other, other_id = _signup(client, "edit-other@example.com")
    ws = _make_workspace(client, owner)
    _add_member_at_role(other_id, ws, Role.MEMBER)
    created = client.post(
        SEARCHES, json=_body(is_shared=True), headers=_scoped(owner, ws)
    ).json()

    resp = client.patch(
        f"{SEARCHES}/{created['id']}", json={"name": "Hijack"}, headers=_scoped(other, ws)
    )
    assert resp.status_code == 403
    assert resp.json()["type"].endswith("/forbidden")


def test_non_owner_cannot_delete_shared_search(client: TestClient) -> None:
    owner, _ = _signup(client, "del-owner@example.com")
    other, other_id = _signup(client, "del-other@example.com")
    ws = _make_workspace(client, owner)
    _add_member_at_role(other_id, ws, Role.MEMBER)
    created = client.post(
        SEARCHES, json=_body(is_shared=True), headers=_scoped(owner, ws)
    ).json()

    resp = client.delete(f"{SEARCHES}/{created['id']}", headers=_scoped(other, ws))
    assert resp.status_code == 403


def test_non_owner_cannot_see_private_to_update_is_404(client: TestClient) -> None:
    owner, _ = _signup(client, "priv-owner@example.com")
    other, other_id = _signup(client, "priv-other@example.com")
    ws = _make_workspace(client, owner)
    _add_member_at_role(other_id, ws, Role.MEMBER)
    created = client.post(
        SEARCHES, json=_body(is_shared=False), headers=_scoped(owner, ws)
    ).json()
    # A search the caller cannot even *see* is 404 (not 403) — existence not leaked.
    resp = client.patch(
        f"{SEARCHES}/{created['id']}", json={"name": "x"}, headers=_scoped(other, ws)
    )
    assert resp.status_code == 404


# --- RBAC: viewers cannot write --------------------------------------------


def test_viewer_cannot_create(client: TestClient) -> None:
    owner, _ = _signup(client, "rbac-owner@example.com")
    viewer, viewer_id = _signup(client, "rbac-viewer@example.com")
    ws = _make_workspace(client, owner)
    _add_member_at_role(viewer_id, ws, Role.VIEWER)
    resp = client.post(SEARCHES, json=_body(), headers=_scoped(viewer, ws))
    assert resp.status_code == 403


def test_viewer_can_list_shared(client: TestClient) -> None:
    owner, _ = _signup(client, "rbacv-owner@example.com")
    viewer, viewer_id = _signup(client, "rbacv-viewer@example.com")
    ws = _make_workspace(client, owner)
    _add_member_at_role(viewer_id, ws, Role.VIEWER)
    created = client.post(
        SEARCHES, json=_body(is_shared=True), headers=_scoped(owner, ws)
    ).json()
    listed = client.get(SEARCHES, headers=_scoped(viewer, ws)).json()
    assert [s["id"] for s in listed["items"]] == [created["id"]]


# --- Update / delete --------------------------------------------------------


def test_rename_and_refilter(client: TestClient) -> None:
    token, _ = _signup(client, "patch@example.com")
    ws = _make_workspace(client, token)
    created = client.post(SEARCHES, json=_body(), headers=_scoped(token, ws)).json()
    resp = client.patch(
        f"{SEARCHES}/{created['id']}",
        json={"name": "Renamed", "filters": {"signal_type": "news_mention"}},
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["filters"] == {"signal_type": "news_mention"}  # whole blob replaced


def test_toggle_share(client: TestClient) -> None:
    token, _ = _signup(client, "togglesharept@example.com")
    ws = _make_workspace(client, token)
    created = client.post(
        SEARCHES, json=_body(is_shared=False), headers=_scoped(token, ws)
    ).json()
    resp = client.patch(
        f"{SEARCHES}/{created['id']}", json={"is_shared": True}, headers=_scoped(token, ws)
    )
    assert resp.status_code == 200
    assert resp.json()["is_shared"] is True


def test_patch_rejects_unknown_key(client: TestClient) -> None:
    token, _ = _signup(client, "patchbad@example.com")
    ws = _make_workspace(client, token)
    created = client.post(SEARCHES, json=_body(), headers=_scoped(token, ws)).json()
    resp = client.patch(
        f"{SEARCHES}/{created['id']}", json={"created_by": "x"}, headers=_scoped(token, ws)
    )
    assert resp.status_code == 422


def test_delete_then_get_is_404(client: TestClient) -> None:
    token, _ = _signup(client, "delete@example.com")
    ws = _make_workspace(client, token)
    created = client.post(SEARCHES, json=_body(), headers=_scoped(token, ws)).json()
    resp = client.delete(f"{SEARCHES}/{created['id']}", headers=_scoped(token, ws))
    assert resp.status_code == 204
    assert client.get(f"{SEARCHES}/{created['id']}", headers=_scoped(token, ws)).status_code == 404


# --- Cursor pagination ------------------------------------------------------


def test_list_is_cursor_paginated(client: TestClient) -> None:
    token, _ = _signup(client, "paginate@example.com")
    ws = _make_workspace(client, token)
    for i in range(3):
        client.post(SEARCHES, json=_body(name=f"S {i}"), headers=_scoped(token, ws))

    page1 = client.get(f"{SEARCHES}?limit=2", headers=_scoped(token, ws))
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None

    page2 = client.get(
        f"{SEARCHES}?limit=2&cursor={body1['next_cursor']}", headers=_scoped(token, ws)
    )
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None
    ids = {s["id"] for s in body1["items"]} | {s["id"] for s in body2["items"]}
    assert len(ids) == 3  # no overlap across pages


def test_list_invalid_cursor_is_400(client: TestClient) -> None:
    token, _ = _signup(client, "badcursor@example.com")
    ws = _make_workspace(client, token)
    resp = client.get(f"{SEARCHES}?cursor=not-a-cursor", headers=_scoped(token, ws))
    assert resp.status_code == 400
