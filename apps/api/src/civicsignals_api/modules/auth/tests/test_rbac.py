"""RBAC dependency tests (B7): role hierarchy + ``require_role`` enforcement.

The ``role_satisfies`` helper is pure logic and always runs. The end-to-end
enforcement tests mount throwaway probe routes (one per role floor) and drive
them with members of varying roles, asserting the per-role access matrix and the
RFC 7807 ``403`` shape. They need Postgres (the ``client`` fixture skips
otherwise; CI provides one). Memberships at non-owner roles are materialized
through ``accounts.services.add_member`` against the test session.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.main import app
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import MembershipRole as Role
from civicsignals_api.modules.auth.dependencies import (
    RequireAdmin,
    RequireMember,
    RequireViewer,
    role_satisfies,
)

from .conftest import _DSN

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
PASSWORD = "s3cure-pa55word"

VIEWER_PROBE = "/api/v1/_test/rbac-viewer"
MEMBER_PROBE = "/api/v1/_test/rbac-member"
ADMIN_PROBE = "/api/v1/_test/rbac-admin"


# --- pure logic (no DB) -----------------------------------------------------


def test_role_hierarchy_is_total_and_ordered() -> None:
    order = [Role.VIEWER, Role.MEMBER, Role.ADMIN, Role.OWNER]
    for i, role in enumerate(order):
        # A role satisfies every floor at or below its rank, and nothing above.
        for j, floor in enumerate(order):
            assert role_satisfies(role, floor) is (i >= j)


def test_each_role_satisfies_itself() -> None:
    for role in Role:
        assert role_satisfies(role, role) is True


def test_owner_satisfies_every_floor() -> None:
    assert all(role_satisfies(Role.OWNER, floor) for floor in Role)


# --- enforcement (DB-backed) ------------------------------------------------


@pytest.fixture(autouse=True)
def _probe_routes() -> Iterator[None]:
    """Mount one probe per role floor; remove them after the test."""

    async def _viewer(ctx: RequireViewer) -> dict[str, str]:
        return {"role": ctx.role.value}

    async def _member(ctx: RequireMember) -> dict[str, str]:
        return {"role": ctx.role.value}

    async def _admin(ctx: RequireAdmin) -> dict[str, str]:
        return {"role": ctx.role.value}

    app.get(VIEWER_PROBE)(_viewer)
    app.get(MEMBER_PROBE)(_member)
    app.get(ADMIN_PROBE)(_admin)
    yield
    probes = {VIEWER_PROBE, MEMBER_PROBE, ADMIN_PROBE}
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) not in probes]


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    """Return ``(access_token, user_id)`` for a fresh user."""
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_member_at_role(user_id: str, workspace_id: str, role: Role) -> None:
    """Insert a membership at ``role`` directly (no invite API yet, B6)."""
    import uuid

    async def _do() -> None:
        engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
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


def _make_workspace_with_members(client: TestClient) -> dict[str, object]:
    """Create a workspace (owner) and add an admin, member, and viewer to it."""
    owner_token, _ = _signup(client, "rbac-owner@example.com")
    ws = client.post(WORKSPACES, json={"name": "RBAC WS"}, headers=_auth(owner_token)).json()

    admin_token, admin_id = _signup(client, "rbac-admin@example.com")
    member_token, member_id = _signup(client, "rbac-member@example.com")
    viewer_token, viewer_id = _signup(client, "rbac-viewer@example.com")
    _add_member_at_role(admin_id, ws["id"], Role.ADMIN)
    _add_member_at_role(member_id, ws["id"], Role.MEMBER)
    _add_member_at_role(viewer_id, ws["id"], Role.VIEWER)
    return {
        "ws_id": ws["id"],
        "owner": owner_token,
        "admin": admin_token,
        "member": member_token,
        "viewer": viewer_token,
    }


def _hdr(token: str, ws_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws_id}


def test_viewer_floor_allows_all_roles(client: TestClient) -> None:
    s = _make_workspace_with_members(client)
    ws_id = str(s["ws_id"])
    for role in ("owner", "admin", "member", "viewer"):
        resp = client.get(VIEWER_PROBE, headers=_hdr(str(s[role]), ws_id))
        assert resp.status_code == 200, (role, resp.text)
        assert resp.json()["role"] == role


def test_member_floor_allows_member_and_up_blocks_viewer(client: TestClient) -> None:
    s = _make_workspace_with_members(client)
    ws_id = str(s["ws_id"])
    for role in ("owner", "admin", "member"):
        assert client.get(MEMBER_PROBE, headers=_hdr(str(s[role]), ws_id)).status_code == 200
    denied = client.get(MEMBER_PROBE, headers=_hdr(str(s["viewer"]), ws_id))
    assert denied.status_code == 403
    body = denied.json()
    assert body["type"].endswith("/forbidden")
    assert body["status"] == 403
    assert denied.headers["content-type"].startswith("application/problem+json")


def test_admin_floor_allows_admin_and_up_blocks_member_and_viewer(client: TestClient) -> None:
    s = _make_workspace_with_members(client)
    ws_id = str(s["ws_id"])
    for role in ("owner", "admin"):
        assert client.get(ADMIN_PROBE, headers=_hdr(str(s[role]), ws_id)).status_code == 200
    for role in ("member", "viewer"):
        denied = client.get(ADMIN_PROBE, headers=_hdr(str(s[role]), ws_id))
        assert denied.status_code == 403, role
        assert denied.json()["type"].endswith("/forbidden")


def test_role_gate_runs_after_workspace_resolution(client: TestClient) -> None:
    # A non-member hitting an admin-gated probe gets 404 (membership fails first),
    # not 403 — existence is never leaked to non-members (doc 08 §1.7).
    s = _make_workspace_with_members(client)
    outsider_token, _ = _signup(client, "rbac-outsider@example.com")
    resp = client.get(ADMIN_PROBE, headers=_hdr(outsider_token, str(s["ws_id"])))
    assert resp.status_code == 404


def test_missing_token_is_401_before_role_check(client: TestClient) -> None:
    resp = client.get(
        ADMIN_PROBE, headers={"X-Workspace-Id": "00000000-0000-7000-8000-000000000000"}
    )
    assert resp.status_code == 401
