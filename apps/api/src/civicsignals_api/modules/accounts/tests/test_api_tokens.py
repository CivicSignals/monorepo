"""End-to-end tests for workspace API tokens + scope enforcement (B8).

Covers the doc 08 §1.3 contract for workspace-scoped tokens:

- create is admin-gated and returns the plaintext **once**; list never reveals it;
- a workspace token authenticates + is bound to its one workspace (it cannot
  reach another tenant even with a matching ``X-Workspace-Id``);
- per-token scopes are enforced (``require_scope`` → 403 on insufficient scope);
- a session/JWT caller is not scope-limited (RBAC role is the gate);
- revoke disables the token immediately;
- member/viewer cannot manage tokens (admin gate).

Needs Postgres (the ``client`` fixture skips otherwise; CI provides one).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Annotated

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.main import app
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import MembershipRole as Role
from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_scope

from .conftest import _require_db

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
PASSWORD = "s3cure-pa55word"

# A throwaway route gated by ``require_scope`` so the scope seam can be exercised
# end-to-end through the real auth dependency chain.
_SCOPED_PATH = "/api/v1/_test/needs-signals-read"


@pytest.fixture(autouse=True)
def _scoped_probe_route() -> Iterator[None]:
    """Mount the scope probe per-test and remove it after (matches test_rbac).

    Registering on the shared ``app`` is done inside a fixture (not at import
    time) so the throwaway route never leaks into other test modules' routing.
    """

    async def _scoped_probe(
        ctx: Annotated[WorkspaceContext, Depends(require_scope("signals:read"))],
    ) -> dict[str, str]:
        return {"workspace_id": str(ctx.workspace_id)}

    app.get(_SCOPED_PATH, include_in_schema=False)(_scoped_probe)
    yield
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != _SCOPED_PATH]


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


@pytest.fixture
def seed(client: TestClient) -> dict[str, str]:
    owner_token, owner_id = _signup(client, "wstok-owner@example.com")
    ws = client.post(WORKSPACES, json={"name": "Token WS"}, headers=_auth(owner_token)).json()
    member_token, member_id = _signup(client, "wstok-member@example.com")
    viewer_token, viewer_id = _signup(client, "wstok-viewer@example.com")
    _add_member_at_role(member_id, ws["id"], Role.MEMBER)
    _add_member_at_role(viewer_id, ws["id"], Role.VIEWER)
    return {
        "ws_id": ws["id"],
        "owner_token": owner_token,
        "owner_id": owner_id,
        "member_token": member_token,
        "viewer_token": viewer_token,
    }


def _tokens_url(ws_id: str) -> str:
    return f"{WORKSPACES}/{ws_id}/api-tokens"


def test_admin_creates_token_revealed_once(seed: dict[str, str], client: TestClient) -> None:
    url = _tokens_url(seed["ws_id"])
    created = client.post(
        url,
        json={"name": "Salesforce push", "scopes": ["signals:read", "signals:write"]},
        headers=_hdr(seed["owner_token"], seed["ws_id"]),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("cs_live_")
    assert body["token_type"] == "workspace"
    assert body["workspace_id"] == seed["ws_id"]
    assert set(body["scopes"]) == {"signals:read", "signals:write"}

    # List returns metadata only — never the secret.
    listing = client.get(url, headers=_hdr(seed["owner_token"], seed["ws_id"]))
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert len(items) == 1
    assert "token" not in items[0]
    assert items[0]["id"] == body["id"]


def test_token_management_is_admin_gated(seed: dict[str, str], client: TestClient) -> None:
    url = _tokens_url(seed["ws_id"])
    for role in ("member_token", "viewer_token"):
        denied = client.post(
            url,
            json={"name": "nope", "scopes": []},
            headers=_hdr(seed[role], seed["ws_id"]),
        )
        assert denied.status_code == 403, role
        listing = client.get(url, headers=_hdr(seed[role], seed["ws_id"]))
        assert listing.status_code == 403, role


def test_workspace_token_enforces_scope(seed: dict[str, str], client: TestClient) -> None:
    url = _tokens_url(seed["ws_id"])
    # Token WITH the scope passes the scoped probe.
    with_scope = client.post(
        url,
        json={"name": "reader", "scopes": ["signals:read"]},
        headers=_hdr(seed["owner_token"], seed["ws_id"]),
    ).json()["token"]
    ok = client.get(_SCOPED_PATH, headers=_auth(with_scope))
    assert ok.status_code == 200, ok.text
    assert ok.json()["workspace_id"] == seed["ws_id"]

    # Token WITHOUT the scope is forbidden (403, not 401).
    without_scope = client.post(
        url,
        json={"name": "writer-only", "scopes": ["signals:write"]},
        headers=_hdr(seed["owner_token"], seed["ws_id"]),
    ).json()["token"]
    denied = client.get(_SCOPED_PATH, headers=_auth(without_scope))
    assert denied.status_code == 403, denied.text
    assert denied.json()["type"].endswith("/insufficient_scope")


def test_session_jwt_not_scope_limited(seed: dict[str, str], client: TestClient) -> None:
    # A session/JWT caller (owner) is gated by role, not scopes, so the scoped
    # probe lets them through with no token scopes at all (doc 08 §1.3).
    ok = client.get(_SCOPED_PATH, headers=_hdr(seed["owner_token"], seed["ws_id"]))
    assert ok.status_code == 200, ok.text


def test_workspace_token_bound_to_one_workspace(seed: dict[str, str], client: TestClient) -> None:
    token = client.post(
        _tokens_url(seed["ws_id"]),
        json={"name": "scoped", "scopes": ["signals:read"]},
        headers=_hdr(seed["owner_token"], seed["ws_id"]),
    ).json()["token"]

    # A different workspace owned by someone else.
    other_owner, _ = _signup(client, "wstok-other@example.com")
    other_ws = client.post(WORKSPACES, json={"name": "Other"}, headers=_auth(other_owner)).json()[
        "id"
    ]

    # No header → resolves to the token's bound workspace.
    no_header = client.get(_SCOPED_PATH, headers=_auth(token))
    assert no_header.status_code == 200
    assert no_header.json()["workspace_id"] == seed["ws_id"]

    # Header pointing at ANOTHER workspace → 403 (the token can't cross tenants).
    cross = client.get(_SCOPED_PATH, headers=_hdr(token, other_ws))
    assert cross.status_code == 403, cross.text
    assert cross.json()["type"].endswith("/forbidden")


def test_revoke_disables_workspace_token(seed: dict[str, str], client: TestClient) -> None:
    url = _tokens_url(seed["ws_id"])
    created = client.post(
        url,
        json={"name": "revoke-me", "scopes": ["signals:read"]},
        headers=_hdr(seed["owner_token"], seed["ws_id"]),
    ).json()
    token, token_id = created["token"], created["id"]

    assert client.get(_SCOPED_PATH, headers=_auth(token)).status_code == 200

    revoked = client.delete(f"{url}/{token_id}", headers=_hdr(seed["owner_token"], seed["ws_id"]))
    assert revoked.status_code == 204

    after = client.get(_SCOPED_PATH, headers=_auth(token))
    assert after.status_code == 401


def test_revoke_unknown_token_is_404(seed: dict[str, str], client: TestClient) -> None:
    ghost = "00000000-0000-7000-8000-000000000000"
    resp = client.delete(
        f"{_tokens_url(seed['ws_id'])}/{ghost}",
        headers=_hdr(seed["owner_token"], seed["ws_id"]),
    )
    assert resp.status_code == 404
