"""Per-role access matrix for the admin-gated member endpoints (B7).

Exercises the real ``/workspaces/{id}/members`` list + role-change endpoints
through the API to prove the RBAC gates: admin-and-up succeeds, member/viewer
get ``403``, non-members get ``404`` (existence not leaked), and the owner's role
is protected. Needs Postgres (the ``client`` fixture skips otherwise; CI has one).
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import MembershipRole as Role

from .conftest import _require_db

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
PASSWORD = "s3cure-pa55word"


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _hdr(token: str, ws_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws_id}


def _add_member_at_role(user_id: str, workspace_id: str, role: Role) -> None:
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


def _seed(client: TestClient) -> dict[str, str]:
    owner_token, owner_id = _signup(client, "mrbac-owner@example.com")
    ws = client.post(WORKSPACES, json={"name": "Member RBAC"}, headers=_auth(owner_token)).json()
    admin_token, admin_id = _signup(client, "mrbac-admin@example.com")
    member_token, member_id = _signup(client, "mrbac-member@example.com")
    viewer_token, viewer_id = _signup(client, "mrbac-viewer@example.com")
    _add_member_at_role(admin_id, ws["id"], Role.ADMIN)
    _add_member_at_role(member_id, ws["id"], Role.MEMBER)
    _add_member_at_role(viewer_id, ws["id"], Role.VIEWER)
    return {
        "ws_id": ws["id"],
        "owner_token": owner_token,
        "owner_id": owner_id,
        "admin_token": admin_token,
        "member_token": member_token,
        "member_id": member_id,
        "viewer_token": viewer_token,
        "viewer_id": viewer_id,
    }


def test_list_members_admin_only(client: TestClient) -> None:
    s = _seed(client)
    url = f"{WORKSPACES}/{s['ws_id']}/members"

    # owner + admin succeed; both see all four members.
    for role in ("owner_token", "admin_token"):
        resp = client.get(url, headers=_hdr(s[role], s["ws_id"]))
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["items"]) == 4

    # member + viewer are forbidden.
    for role in ("member_token", "viewer_token"):
        denied = client.get(url, headers=_hdr(s[role], s["ws_id"]))
        assert denied.status_code == 403, role
        assert denied.json()["type"].endswith("/forbidden")


def test_list_members_non_member_is_404(client: TestClient) -> None:
    s = _seed(client)
    outsider, _ = _signup(client, "mrbac-outsider@example.com")
    resp = client.get(f"{WORKSPACES}/{s['ws_id']}/members", headers=_hdr(outsider, s["ws_id"]))
    assert resp.status_code == 404


def test_admin_can_change_member_role_others_cannot(client: TestClient) -> None:
    s = _seed(client)
    url = f"{WORKSPACES}/{s['ws_id']}/members/{s['viewer_id']}"

    # member cannot promote the viewer.
    denied = client.patch(url, json={"role": "member"}, headers=_hdr(s["member_token"], s["ws_id"]))
    assert denied.status_code == 403

    # viewer cannot either.
    denied2 = client.patch(url, json={"role": "admin"}, headers=_hdr(s["viewer_token"], s["ws_id"]))
    assert denied2.status_code == 403

    # admin can promote the viewer to member.
    ok = client.patch(url, json={"role": "member"}, headers=_hdr(s["admin_token"], s["ws_id"]))
    assert ok.status_code == 200, ok.text
    assert ok.json()["role"] == "member"
    assert ok.json()["user_id"] == s["viewer_id"]


def test_owner_role_cannot_be_changed(client: TestClient) -> None:
    s = _seed(client)
    url = f"{WORKSPACES}/{s['ws_id']}/members/{s['owner_id']}"
    resp = client.patch(url, json={"role": "member"}, headers=_hdr(s["admin_token"], s["ws_id"]))
    assert resp.status_code == 403
    assert resp.json()["type"].endswith("/forbidden")


def test_update_unknown_member_is_404(client: TestClient) -> None:
    s = _seed(client)
    ghost = "00000000-0000-7000-8000-000000000000"
    url = f"{WORKSPACES}/{s['ws_id']}/members/{ghost}"
    resp = client.patch(url, json={"role": "member"}, headers=_hdr(s["admin_token"], s["ws_id"]))
    assert resp.status_code == 404


def test_role_change_isolated_across_workspaces(client: TestClient) -> None:
    # An admin of WS-A cannot manage members via a path pointing at WS-B; and the
    # path workspace must match the active (X-Workspace-Id) workspace.
    s = _seed(client)
    other_owner, _ = _signup(client, "mrbac-otherowner@example.com")
    other_ws = client.post(WORKSPACES, json={"name": "Other WS"}, headers=_auth(other_owner)).json()

    # admin of WS-A, addressing WS-B in the path while scoped to WS-A -> 403 mismatch.
    url = f"{WORKSPACES}/{other_ws['id']}/members/{s['viewer_id']}"
    resp = client.patch(url, json={"role": "member"}, headers=_hdr(s["admin_token"], s["ws_id"]))
    assert resp.status_code == 403

    # admin of WS-A scoped to WS-B (not a member there) -> 404.
    resp2 = client.patch(
        url, json={"role": "member"}, headers=_hdr(s["admin_token"], other_ws["id"])
    )
    assert resp2.status_code == 404
