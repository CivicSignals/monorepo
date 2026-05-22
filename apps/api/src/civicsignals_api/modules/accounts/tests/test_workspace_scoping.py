"""Tests for the ``require_workspace`` scoping seam (B5, doc 08 §1.4).

The dependency is what every workspace-scoped module (B6/B7/B9/F1/J1/N1/C3)
depends on, so it gets its own coverage via a throwaway probe route mounted on
the app. Needs Postgres (the ``client`` fixture skips otherwise).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from civicsignals_api.main import app
from civicsignals_api.modules.auth.dependencies import CurrentWorkspace

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
PASSWORD = "s3cure-pa55word"
PROBE_PATH = "/api/v1/_test/scoped-probe"


@pytest.fixture(autouse=True)
def _probe_route() -> Iterator[None]:
    """Mount a probe endpoint that echoes the resolved workspace id + role."""

    @app.get(PROBE_PATH)
    async def _scoped_probe(ctx: CurrentWorkspace) -> dict[str, str]:
        return {"workspace_id": str(ctx.workspace_id), "role": ctx.role.value}

    yield
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != PROBE_PATH]


def _signup(client: TestClient, email: str) -> str:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tokens"]["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_header_selects_the_workspace(client: TestClient) -> None:
    token = _signup(client, "scope-header@example.com")
    ws = client.post(WORKSPACES, json={"name": "Scoped"}, headers=_auth(token)).json()

    resp = client.get(PROBE_PATH, headers={**_auth(token), "X-Workspace-Id": ws["id"]})
    assert resp.status_code == 200
    assert resp.json() == {"workspace_id": ws["id"], "role": "owner"}


def test_falls_back_to_last_active_when_header_absent(client: TestClient) -> None:
    token = _signup(client, "scope-fallback@example.com")
    ws = client.post(WORKSPACES, json={"name": "Fallback"}, headers=_auth(token)).json()
    # Without a switch there is no last_active yet -> 400.
    assert client.get(PROBE_PATH, headers=_auth(token)).status_code == 400

    client.post(f"{WORKSPACES}/{ws['id']}/switch", headers=_auth(token))
    resp = client.get(PROBE_PATH, headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["workspace_id"] == ws["id"]


def test_non_member_header_is_404(client: TestClient) -> None:
    owner = _signup(client, "scope-owner@example.com")
    outsider = _signup(client, "scope-outsider@example.com")
    ws = client.post(WORKSPACES, json={"name": "Theirs"}, headers=_auth(owner)).json()

    resp = client.get(PROBE_PATH, headers={**_auth(outsider), "X-Workspace-Id": ws["id"]})
    assert resp.status_code == 404


def test_malformed_header_is_400(client: TestClient) -> None:
    token = _signup(client, "scope-bad@example.com")
    resp = client.get(PROBE_PATH, headers={**_auth(token), "X-Workspace-Id": "not-a-uuid"})
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("/bad_request")
