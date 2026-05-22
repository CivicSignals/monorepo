"""End-to-end ICP flow tests (F1): CRUD, workspace isolation, validation,
active toggling, cursor pagination.

These need Postgres (``TEXT[]`` arrays + GIN indexes, JSONB); the ``client``
fixture skips when no DSN is configured. CI provides one.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
ICP = "/api/v1/icp"

PASSWORD = "s3cure-pa55word"


def _signup(client: TestClient, email: str) -> str:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tokens"]["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _make_workspace(client: TestClient, token: str, name: str = "Acme SLED") -> str:
    resp = client.post(WORKSPACES, json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _scoped(token: str, workspace_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": workspace_id}


def _valid_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": "Office software for US K-12",
        "countries": ["US"],
        "states": ["WA", "OR"],
        "entity_kinds": ["k12_district"],
        "signal_types": ["rfp_posted", "grant_awarded"],
        "min_size": 2000,
        "max_size": 100000,
        "signal_weights": {"rfp_posted": 1.0, "grant_awarded": 0.8},
        "keywords_required": ["productivity software"],
        "keywords_excluded": ["athletics"],
        "deal_band_min_cents": 1000000,
        "deal_band_max_cents": 100000000,
        "threshold": 50,
    }
    body.update(overrides)
    return body


# --- Create / read ----------------------------------------------------------


def test_create_icp(client: TestClient) -> None:
    token = _signup(client, "create@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(ICP, json=_valid_body(), headers=_scoped(token, ws))
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Office software for US K-12"
    assert body["workspace_id"] == ws
    assert body["countries"] == ["US"]
    assert body["states"] == ["WA", "OR"]
    assert body["signal_weights"] == {"rfp_posted": 1.0, "grant_awarded": 0.8}
    assert body["is_active"] is True
    assert resp.headers["location"].endswith(f"/icp/{body['id']}")


def test_create_requires_workspace_header(client: TestClient) -> None:
    token = _signup(client, "noheader@example.com")
    _make_workspace(client, token)  # exists but header absent and no last_active
    resp = client.post(ICP, json=_valid_body(), headers=_auth(token))
    assert resp.status_code == 400


def test_create_requires_auth(client: TestClient) -> None:
    resp = client.post(ICP, json=_valid_body())
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_get_icp(client: TestClient) -> None:
    token = _signup(client, "get@example.com")
    ws = _make_workspace(client, token)
    created = client.post(ICP, json=_valid_body(), headers=_scoped(token, ws)).json()
    resp = client.get(f"{ICP}/{created['id']}", headers=_scoped(token, ws))
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_unknown_icp_is_404(client: TestClient) -> None:
    token = _signup(client, "ghost@example.com")
    ws = _make_workspace(client, token)
    resp = client.get(f"{ICP}/00000000-0000-7000-8000-000000000000", headers=_scoped(token, ws))
    assert resp.status_code == 404
    assert resp.json()["type"].endswith("/not_found")


# --- Workspace isolation ----------------------------------------------------


def test_workspace_a_cannot_see_workspace_b_icp(client: TestClient) -> None:
    owner = _signup(client, "iso-owner@example.com")
    ws_a = _make_workspace(client, owner, "WS A")
    ws_b = _make_workspace(client, owner, "WS B")

    created = client.post(ICP, json=_valid_body(), headers=_scoped(owner, ws_a)).json()

    # Same user, different workspace context -> the ICP is invisible.
    resp = client.get(f"{ICP}/{created['id']}", headers=_scoped(owner, ws_b))
    assert resp.status_code == 404

    listed = client.get(ICP, headers=_scoped(owner, ws_b)).json()
    assert listed["items"] == []


def test_outsider_cannot_reach_workspace_icp(client: TestClient) -> None:
    owner = _signup(client, "iso2-owner@example.com")
    outsider = _signup(client, "iso2-outsider@example.com")
    ws = _make_workspace(client, owner)
    created = client.post(ICP, json=_valid_body(), headers=_scoped(owner, ws)).json()

    # A non-member of the workspace gets 404 on the X-Workspace-Id (existence
    # not leaked across tenants, doc 08 §1.7).
    resp = client.get(f"{ICP}/{created['id']}", headers=_scoped(outsider, ws))
    assert resp.status_code == 404


def test_update_other_workspace_icp_is_404(client: TestClient) -> None:
    owner = _signup(client, "iso3@example.com")
    ws_a = _make_workspace(client, owner, "A3")
    ws_b = _make_workspace(client, owner, "B3")
    created = client.post(ICP, json=_valid_body(), headers=_scoped(owner, ws_a)).json()

    resp = client.patch(
        f"{ICP}/{created['id']}", json={"name": "Hijack"}, headers=_scoped(owner, ws_b)
    )
    assert resp.status_code == 404


# --- Validation -------------------------------------------------------------


def test_unknown_signal_type_is_422(client: TestClient) -> None:
    token = _signup(client, "badtype@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(
        ICP, json=_valid_body(signal_types=["not_a_signal"]), headers=_scoped(token, ws)
    )
    assert resp.status_code == 422


def test_unknown_weight_key_is_422(client: TestClient) -> None:
    token = _signup(client, "badweight@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(
        ICP, json=_valid_body(signal_weights={"bogus": 0.5}), headers=_scoped(token, ws)
    )
    assert resp.status_code == 422


def test_out_of_range_weight_is_422(client: TestClient) -> None:
    token = _signup(client, "weight-range@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(
        ICP, json=_valid_body(signal_weights={"rfp_posted": 1.5}), headers=_scoped(token, ws)
    )
    assert resp.status_code == 422


def test_out_of_range_threshold_is_422(client: TestClient) -> None:
    token = _signup(client, "threshold@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(ICP, json=_valid_body(threshold=200), headers=_scoped(token, ws))
    assert resp.status_code == 422


def test_inverted_size_band_is_422(client: TestClient) -> None:
    token = _signup(client, "band@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(
        ICP, json=_valid_body(min_size=100000, max_size=2000), headers=_scoped(token, ws)
    )
    assert resp.status_code == 422


def test_inverted_deal_band_is_422(client: TestClient) -> None:
    token = _signup(client, "dealband@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(
        ICP,
        json=_valid_body(deal_band_min_cents=100, deal_band_max_cents=10),
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 422


def test_country_is_normalized_uppercase(client: TestClient) -> None:
    token = _signup(client, "country@example.com")
    ws = _make_workspace(client, token)
    resp = client.post(ICP, json=_valid_body(countries=["us"]), headers=_scoped(token, ws))
    assert resp.status_code == 201
    assert resp.json()["countries"] == ["US"]


# --- Update -----------------------------------------------------------------


def test_patch_updates_fields(client: TestClient) -> None:
    token = _signup(client, "patch@example.com")
    ws = _make_workspace(client, token)
    created = client.post(ICP, json=_valid_body(), headers=_scoped(token, ws)).json()

    resp = client.patch(
        f"{ICP}/{created['id']}",
        json={"name": "Renamed", "states": ["TX"], "threshold": 70},
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["states"] == ["TX"]
    assert body["threshold"] == 70
    # Untouched fields are preserved.
    assert body["signal_types"] == ["rfp_posted", "grant_awarded"]


def test_patch_explicit_null_leaves_field_unchanged(client: TestClient) -> None:
    token = _signup(client, "patchnull@example.com")
    ws = _make_workspace(client, token)
    created = client.post(ICP, json=_valid_body(), headers=_scoped(token, ws)).json()
    # An explicit null on a NOT NULL array column means "leave unchanged" — it
    # must not 500 by writing NULL, and the stored value is preserved.
    resp = client.patch(
        f"{ICP}/{created['id']}",
        json={"countries": None, "name": "Kept Arrays"},
        headers=_scoped(token, ws),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Kept Arrays"
    assert body["countries"] == ["US"]  # unchanged


def test_patch_inverted_band_against_stored_row_is_422(client: TestClient) -> None:
    token = _signup(client, "patchband@example.com")
    ws = _make_workspace(client, token)
    # Stored max_size=100000; patching min_size above it must fail the
    # service-layer cross-check against the stored row.
    created = client.post(ICP, json=_valid_body(), headers=_scoped(token, ws)).json()
    resp = client.patch(
        f"{ICP}/{created['id']}", json={"min_size": 200000}, headers=_scoped(token, ws)
    )
    assert resp.status_code == 422


def test_patch_cannot_set_is_active(client: TestClient) -> None:
    token = _signup(client, "patchactive@example.com")
    ws = _make_workspace(client, token)
    created = client.post(ICP, json=_valid_body(), headers=_scoped(token, ws)).json()
    # is_active is not a patchable field (extra=forbid -> 422).
    resp = client.patch(
        f"{ICP}/{created['id']}", json={"is_active": False}, headers=_scoped(token, ws)
    )
    assert resp.status_code == 422


# --- Active toggling --------------------------------------------------------


def test_creating_second_active_icp_deactivates_first(client: TestClient) -> None:
    token = _signup(client, "twoactive@example.com")
    ws = _make_workspace(client, token)
    first = client.post(ICP, json=_valid_body(name="First"), headers=_scoped(token, ws)).json()
    second = client.post(ICP, json=_valid_body(name="Second"), headers=_scoped(token, ws)).json()

    assert second["is_active"] is True
    refreshed_first = client.get(f"{ICP}/{first['id']}", headers=_scoped(token, ws)).json()
    assert refreshed_first["is_active"] is False


def test_activate_flips_the_active_one(client: TestClient) -> None:
    token = _signup(client, "activate@example.com")
    ws = _make_workspace(client, token)
    first = client.post(ICP, json=_valid_body(name="First"), headers=_scoped(token, ws)).json()
    second = client.post(ICP, json=_valid_body(name="Second"), headers=_scoped(token, ws)).json()

    # Re-activate the first; the second must become inactive.
    resp = client.post(f"{ICP}/{first['id']}/activate", headers=_scoped(token, ws))
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True
    refreshed_second = client.get(f"{ICP}/{second['id']}", headers=_scoped(token, ws)).json()
    assert refreshed_second["is_active"] is False


def test_deactivate(client: TestClient) -> None:
    token = _signup(client, "deactivate@example.com")
    ws = _make_workspace(client, token)
    created = client.post(ICP, json=_valid_body(), headers=_scoped(token, ws)).json()
    resp = client.post(f"{ICP}/{created['id']}/deactivate", headers=_scoped(token, ws))
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False


def test_create_inactive_does_not_disturb_active(client: TestClient) -> None:
    token = _signup(client, "inactive@example.com")
    ws = _make_workspace(client, token)
    active = client.post(ICP, json=_valid_body(name="Active"), headers=_scoped(token, ws)).json()
    # An explicitly-inactive ICP leaves the existing active one alone.
    client.post(ICP, json=_valid_body(name="Draft", is_active=False), headers=_scoped(token, ws))
    refreshed = client.get(f"{ICP}/{active['id']}", headers=_scoped(token, ws)).json()
    assert refreshed["is_active"] is True


# --- Cursor pagination ------------------------------------------------------


def test_list_is_cursor_paginated(client: TestClient) -> None:
    token = _signup(client, "paginate@example.com")
    ws = _make_workspace(client, token)
    for i in range(3):
        # Only the last stays active; all three rows persist regardless.
        client.post(ICP, json=_valid_body(name=f"ICP {i}"), headers=_scoped(token, ws))

    page1 = client.get(f"{ICP}?limit=2", headers=_scoped(token, ws))
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["next_cursor"] is not None

    page2 = client.get(f"{ICP}?limit=2&cursor={body1['next_cursor']}", headers=_scoped(token, ws))
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["next_cursor"] is None
    ids = {i["id"] for i in body1["items"]} | {i["id"] for i in body2["items"]}
    assert len(ids) == 3  # no overlap across pages


def test_list_invalid_cursor_is_400(client: TestClient) -> None:
    token = _signup(client, "badcursor@example.com")
    ws = _make_workspace(client, token)
    resp = client.get(f"{ICP}?cursor=not-a-cursor", headers=_scoped(token, ws))
    assert resp.status_code == 400


def test_list_returns_only_this_workspaces_icps(client: TestClient) -> None:
    token = _signup(client, "listiso@example.com")
    ws_a = _make_workspace(client, token, "List A")
    ws_b = _make_workspace(client, token, "List B")
    client.post(ICP, json=_valid_body(name="A only"), headers=_scoped(token, ws_a))
    client.post(ICP, json=_valid_body(name="B only"), headers=_scoped(token, ws_b))

    listed_a = client.get(ICP, headers=_scoped(token, ws_a)).json()
    names = [i["name"] for i in listed_a["items"]]
    assert names == ["A only"]
