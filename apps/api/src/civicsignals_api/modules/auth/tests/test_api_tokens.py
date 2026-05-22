"""End-to-end tests for personal access tokens + the API-token auth path (B8).

Covers the doc 08 §1.3 contract:

- create returns the plaintext **once**; list/get never expose it again;
- an API token authenticates as the owning user (the same identity a JWT does);
- ``last_used_at`` is bumped on use;
- revocation disables the token immediately;
- the catalog of grantable scopes is published.

Needs Postgres (the ``client`` fixture skips otherwise; CI provides one).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

SIGNUP = "/api/v1/auth/signup"
TOKENS = "/api/v1/auth/tokens"
ME = "/api/v1/auth/me"
PASSWORD = "s3cure-pa55word"


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_create_pat_reveals_secret_once(client: TestClient) -> None:
    jwt, user_id = _signup(client, "pat-once@example.com")

    created = client.post(
        TOKENS,
        json={"name": "CLI token", "scopes": ["signals:read"]},
        headers=_auth(jwt),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    secret = body["token"]
    assert secret.startswith("cs_pat_")
    assert body["token_type"] == "personal"
    assert body["user_id"] == user_id
    assert body["workspace_id"] is None
    assert body["scopes"] == ["signals:read"]
    assert body["revoked_at"] is None
    # The non-secret prefix is a fragment of the secret, not the whole thing.
    assert secret.startswith(body["token_prefix"])
    assert body["token_prefix"] != secret

    # List never returns the secret.
    listing = client.get(TOKENS, headers=_auth(jwt))
    assert listing.status_code == 200, listing.text
    items = listing.json()["items"]
    assert len(items) == 1
    assert "token" not in items[0]
    assert items[0]["id"] == body["id"]
    assert items[0]["token_prefix"] == body["token_prefix"]


def test_pat_authenticates_as_owner(client: TestClient) -> None:
    jwt, user_id = _signup(client, "pat-identity@example.com")
    created = client.post(TOKENS, json={"name": "ident", "scopes": []}, headers=_auth(jwt))
    secret = created.json()["token"]

    # The PAT resolves to the same user as the JWT.
    me = client.get(ME, headers=_auth(secret))
    assert me.status_code == 200, me.text
    assert me.json()["id"] == user_id
    assert me.json()["email"] == "pat-identity@example.com"


def test_pat_last_used_at_updates(client: TestClient) -> None:
    jwt, _ = _signup(client, "pat-lastused@example.com")
    created = client.post(TOKENS, json={"name": "hygiene", "scopes": []}, headers=_auth(jwt))
    token_id = created.json()["id"]
    secret = created.json()["token"]
    assert created.json()["last_used_at"] is None

    # Use the token, then check the metadata reflects it.
    assert client.get(ME, headers=_auth(secret)).status_code == 200

    listing = client.get(TOKENS, headers=_auth(jwt)).json()["items"]
    used = next(t for t in listing if t["id"] == token_id)
    assert used["last_used_at"] is not None


def test_revoke_disables_pat(client: TestClient) -> None:
    jwt, _ = _signup(client, "pat-revoke@example.com")
    created = client.post(TOKENS, json={"name": "revoke-me", "scopes": []}, headers=_auth(jwt))
    token_id = created.json()["id"]
    secret = created.json()["token"]

    # Works before revocation.
    assert client.get(ME, headers=_auth(secret)).status_code == 200

    revoked = client.delete(f"{TOKENS}/{token_id}", headers=_auth(jwt))
    assert revoked.status_code == 204

    # Immediately rejected after revocation.
    after = client.get(ME, headers=_auth(secret))
    assert after.status_code == 401
    assert after.json()["type"].endswith("/unauthorized")

    # Revoke is idempotent.
    again = client.delete(f"{TOKENS}/{token_id}", headers=_auth(jwt))
    assert again.status_code == 204

    # Still listed (as revoked metadata) for audit.
    listing = client.get(TOKENS, headers=_auth(jwt)).json()["items"]
    revoked_row = next(t for t in listing if t["id"] == token_id)
    assert revoked_row["revoked_at"] is not None


def test_pat_isolated_per_owner(client: TestClient) -> None:
    jwt_a, _ = _signup(client, "pat-owner-a@example.com")
    jwt_b, _ = _signup(client, "pat-owner-b@example.com")
    created = client.post(TOKENS, json={"name": "a-token", "scopes": []}, headers=_auth(jwt_a))
    token_id = created.json()["id"]

    # B cannot see A's token in their own list.
    b_list = client.get(TOKENS, headers=_auth(jwt_b)).json()["items"]
    assert all(t["id"] != token_id for t in b_list)

    # B cannot revoke A's token (404, existence not leaked).
    denied = client.delete(f"{TOKENS}/{token_id}", headers=_auth(jwt_b))
    assert denied.status_code == 404


def test_invalid_scope_rejected(client: TestClient) -> None:
    jwt, _ = _signup(client, "pat-badscope@example.com")
    resp = client.post(
        TOKENS,
        json={"name": "bad", "scopes": ["signals:read", "not:a:scope"]},
        headers=_auth(jwt),
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["type"].endswith("/invalid_scope")
    assert any(e["message"] == "not:a:scope" for e in body["errors"])


def test_scopes_catalog_published(client: TestClient) -> None:
    jwt, _ = _signup(client, "pat-scopes@example.com")
    resp = client.get(f"{TOKENS}/scopes", headers=_auth(jwt))
    assert resp.status_code == 200, resp.text
    scopes = resp.json()["scopes"]
    assert "signals:read" in scopes
    assert "webhooks:manage" in scopes


def test_unknown_api_token_is_401(client: TestClient) -> None:
    bogus = "cs_pat_thisisnotarealtoken000000000000"
    resp = client.get(ME, headers=_auth(bogus))
    assert resp.status_code == 401
    assert resp.json()["type"].endswith("/unauthorized")
