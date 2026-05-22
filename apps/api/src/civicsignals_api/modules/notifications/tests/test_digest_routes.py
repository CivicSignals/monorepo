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


# --- H5: per-user preferences list + one-click unsubscribe ------------------- #

UNSUB = "/api/v1/notifications/digests/unsubscribe"
LIST = "/api/v1/notifications/digests"


def _set_digest(client: TestClient, token: str, ws: str, search_id: str, freq: str) -> str:
    resp = client.put(
        _digest_path(search_id),
        json={"frequency": freq},
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 200, resp.text
    return str(resp.json()["id"])


def test_list_user_subscriptions(client: TestClient) -> None:
    token = _signup(client, "prefs@example.com")
    ws = _make_workspace(client, token)
    s1 = _make_search(client, token, ws)
    s2 = _make_search(client, token, ws)
    _set_digest(client, token, ws, s1, "daily")
    _set_digest(client, token, ws, s2, "weekly")

    resp = client.get(LIST, headers=_scoped(token, ws))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2
    # Every row carries the saved-search name + the current frequency.
    names = {it["saved_search_name"] for it in items}
    assert names == {"Hot RFPs"}
    freqs = {it["frequency"] for it in items}
    assert freqs == {"daily", "weekly"}


def test_list_user_subscriptions_requires_auth(client: TestClient) -> None:
    assert client.get(LIST).status_code == 401


def _mint_token_for(sub_id: str) -> str:
    """Mint a real one-click token for a subscription id (mirrors the email path)."""
    import uuid as _uuid

    from civicsignals_api.modules.notifications.unsubscribe import make_unsubscribe_token

    return make_unsubscribe_token(_uuid.UUID(sub_id))


def test_one_click_unsubscribe_sets_off(client: TestClient) -> None:
    token = _signup(client, "unsub@example.com")
    ws = _make_workspace(client, token)
    search_id = _make_search(client, token, ws)
    sub_id = _set_digest(client, token, ws, search_id, "daily")

    unsub_token = _mint_token_for(sub_id)
    # RFC 8058 one-click POST — no auth header.
    resp = client.post(f"{UNSUB}?token={unsub_token}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["unsubscribed"] is True

    # The subscription is now off (read it back as the owner).
    got = client.get(_digest_path(search_id), headers=_scoped(token, ws))
    assert got.status_code == 200
    assert got.json()["frequency"] == "off"


def test_unsubscribe_landing_get_also_works(client: TestClient) -> None:
    token = _signup(client, "unsub-get@example.com")
    ws = _make_workspace(client, token)
    search_id = _make_search(client, token, ws)
    sub_id = _set_digest(client, token, ws, search_id, "weekly")

    resp = client.get(f"{UNSUB}?token={_mint_token_for(sub_id)}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["unsubscribed"] is True
    assert body["saved_search_name"] == "Hot RFPs"


def test_tampered_token_is_rejected(client: TestClient) -> None:
    token = _signup(client, "tamper@example.com")
    ws = _make_workspace(client, token)
    search_id = _make_search(client, token, ws)
    sub_id = _set_digest(client, token, ws, search_id, "daily")

    good = _mint_token_for(sub_id)
    tampered = good[:-1] + ("0" if good[-1] != "0" else "1")
    resp = client.post(f"{UNSUB}?token={tampered}")
    assert resp.status_code == 400

    # The subscription is untouched.
    got = client.get(_digest_path(search_id), headers=_scoped(token, ws))
    assert got.json()["frequency"] == "daily"


def test_unrelated_token_cannot_unsubscribe_another_subscription(client: TestClient) -> None:
    """A token minted for subscription A only ever turns A off, never B."""
    token = _signup(client, "two-subs@example.com")
    ws = _make_workspace(client, token)
    s_a = _make_search(client, token, ws)
    s_b = _make_search(client, token, ws)
    sub_a = _set_digest(client, token, ws, s_a, "daily")
    _set_digest(client, token, ws, s_b, "weekly")

    # Unsubscribe using A's token.
    resp = client.post(f"{UNSUB}?token={_mint_token_for(sub_a)}")
    assert resp.status_code == 200

    # A is off; B is untouched.
    assert client.get(_digest_path(s_a), headers=_scoped(token, ws)).json()["frequency"] == "off"
    assert client.get(_digest_path(s_b), headers=_scoped(token, ws)).json()["frequency"] == "weekly"
