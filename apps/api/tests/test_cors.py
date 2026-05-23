# SPDX-License-Identifier: AGPL-3.0-only
"""CORS middleware tests (browser → API).

The web app issues client-side fetches from the browser to the API on a different
origin (web :3000 → api :8000). Without CORS the browser blocks every authed call
and login never succeeds (the full-stack e2e blocker). These tests assert the
``CORSMiddleware`` is wired so the dev web origin is allowed (incl. the ``OPTIONS``
preflight) and a disallowed origin is not. DB-free: they only touch ``/healthz``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from civicsignals_api.main import app

_ALLOWED_ORIGIN = "http://localhost:3000"
_DISALLOWED_ORIGIN = "http://evil.example.com"


def test_simple_request_gets_cors_header_for_allowed_origin() -> None:
    client = TestClient(app)
    resp = client.get("/healthz", headers={"Origin": _ALLOWED_ORIGIN})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == _ALLOWED_ORIGIN
    # allow_credentials=True must be reflected so the browser accepts cookies/bearer.
    assert resp.headers.get("access-control-allow-credentials") == "true"


def test_preflight_options_allows_authorization_and_workspace_header() -> None:
    client = TestClient(app)
    resp = client.options(
        "/healthz",
        headers={
            "Origin": _ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "GET",
            # The two headers the SDKs send on authed, workspace-scoped calls.
            "Access-Control-Request-Headers": "Authorization, X-Workspace-Id",
        },
    )
    # Starlette's CORSMiddleware answers the preflight itself with 200.
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == _ALLOWED_ORIGIN
    allowed_headers = resp.headers.get("access-control-allow-headers", "").lower()
    assert "authorization" in allowed_headers
    assert "x-workspace-id" in allowed_headers


def test_disallowed_origin_gets_no_cors_header() -> None:
    client = TestClient(app)
    resp = client.get("/healthz", headers={"Origin": _DISALLOWED_ORIGIN})
    # The request still succeeds server-side, but the browser would block the
    # response because no matching Access-Control-Allow-Origin is present.
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") != _DISALLOWED_ORIGIN
