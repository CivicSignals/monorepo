"""HTTP route tests for the digest schedule endpoints (H3).

Drives the FastAPI app via ``TestClient`` (the ``client`` fixture). Needs Postgres;
skips when no DSN is configured (CI provides one).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
SEARCHES = "/api/v1/searches"
PASSWORD = "s3cure-pa55word"


def _signup(client: TestClient, email: str) -> str:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tokens"]["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scoped(token: str, ws: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws}


def _make_workspace(client: TestClient, token: str) -> str:
    resp = client.post(WORKSPACES, json={"name": "Acme"}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _make_search(client: TestClient, token: str, ws: str) -> str:
    resp = client.post(SEARCHES, json={"name": "Hot RFPs"}, headers=_scoped(token, ws))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _digest_path(search_id: str) -> str:
    return f"/api/v1/notifications/digests/{search_id}"


def test_set_and_get_digest(client: TestClient) -> None:
    token = _signup(client, "digest@example.com")
    ws = _make_workspace(client, token)
    search_id = _make_search(client, token, ws)

    # No digest yet -> 404.
    miss = client.get(_digest_path(search_id), headers=_scoped(token, ws))
    assert miss.status_code == 404

    # Set a daily digest.
    resp = client.put(
        _digest_path(search_id),
        json={"frequency": "daily", "send_hour": 9, "timezone": "America/New_York"},
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["frequency"] == "daily"
    assert body["send_hour"] == 9
    assert body["timezone"] == "America/New_York"
    assert body["saved_search_id"] == search_id

    # Read it back.
    got = client.get(_digest_path(search_id), headers=_scoped(token, ws))
    assert got.status_code == 200
    assert got.json()["frequency"] == "daily"

    # Update (idempotent upsert) to weekly.
    resp2 = client.put(
        _digest_path(search_id),
        json={"frequency": "weekly", "weekday": 2, "send_hour": 6},
        headers=_scoped(token, ws),
    )
    assert resp2.status_code == 200
    assert resp2.json()["frequency"] == "weekly"
    assert resp2.json()["weekday"] == 2


def test_set_digest_rejects_unknown_timezone(client: TestClient) -> None:
    token = _signup(client, "badtz@example.com")
    ws = _make_workspace(client, token)
    search_id = _make_search(client, token, ws)

    resp = client.put(
        _digest_path(search_id),
        json={"frequency": "daily", "timezone": "Not/AZone"},
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 422, resp.text


def test_set_digest_unknown_search_is_404(client: TestClient) -> None:
    token = _signup(client, "nosearch@example.com")
    ws = _make_workspace(client, token)
    resp = client.put(
        _digest_path("00000000-0000-0000-0000-000000000000"),
        json={"frequency": "daily"},
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 404


def test_set_digest_requires_auth(client: TestClient) -> None:
    resp = client.put(
        _digest_path("00000000-0000-0000-0000-000000000000"),
        json={"frequency": "daily"},
    )
    assert resp.status_code == 401
