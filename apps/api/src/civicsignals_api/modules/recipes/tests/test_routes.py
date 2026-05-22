"""Tests for the staff recipe preview endpoint (TODO D5).

Exercises ``POST /api/v1/recipes/preview`` via FastAPI's TestClient: the staff
gate (dev-open / token-gated / fail-closed), happy-path preview by id and inline
YAML, a captured required-field miss, and RFC 7807 problem responses for bad
input / unknown recipe.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from civicsignals_api.config import get_settings
from civicsignals_api.main import create_app

PREVIEW_PATH = "/api/v1/recipes/preview"

INLINE_RECIPE = """\
recipe_id: route-recipe
connector: http_static
version: 1
entity:
  name: Route Agency
  state: WA
fields:
  title:
    selectors:
      - "h1.title"
    required: true
signal_types:
  - rfp_posted
"""

GOOD_HTML = '<html><body><h1 class="title">Route RFP</h1></body></html>'
BAD_HTML = "<html><body><p>no title</p></body></html>"


@pytest.fixture
def client() -> Iterator[TestClient]:
    get_settings.cache_clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_preview_inline_yaml_html(client: TestClient) -> None:
    resp = client.post(PREVIEW_PATH, json={"recipe_yaml": INLINE_RECIPE, "html": GOOD_HTML})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["recipe_id"] == "route-recipe"
    titles = {f["name"]: f["value"] for f in body["fields"]}
    assert titles["title"] == "Route RFP"


def test_preview_by_id(client: TestClient) -> None:
    resp = client.post(PREVIEW_PATH, json={"recipe_id": "wa-state-webs", "html": GOOD_HTML})
    # `wa-state-webs` requires `title` via its own selectors, which GOOD_HTML
    # doesn't match -> ok=False but still a 200 with diagnostics.
    assert resp.status_code == 200
    assert resp.json()["recipe_id"] == "wa-state-webs"


def test_preview_missing_required_returns_200_with_error(client: TestClient) -> None:
    resp = client.post(PREVIEW_PATH, json={"recipe_yaml": INLINE_RECIPE, "html": BAD_HTML})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["error"] and "title" in body["error"]


def test_preview_unknown_recipe_404(client: TestClient) -> None:
    resp = client.post(PREVIEW_PATH, json={"recipe_id": "does-not-exist", "html": GOOD_HTML})
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["title"] == "Recipe not found"


def test_preview_invalid_recipe_400(client: TestClient) -> None:
    resp = client.post(
        PREVIEW_PATH,
        json={"recipe_yaml": "recipe_id: x\nconnector: c\n", "html": GOOD_HTML},
    )
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_preview_bad_input_combo_400(client: TestClient) -> None:
    # Neither html nor url -> bad request.
    resp = client.post(PREVIEW_PATH, json={"recipe_yaml": INLINE_RECIPE})
    assert resp.status_code == 400
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_preview_fetch_failure_maps_to_502(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A FetchFailedError from the service maps to a 502 by exception *type*
    # (no message-prefix matching).
    from civicsignals_api.modules.recipes import services
    from civicsignals_api.modules.recipes.runner import FetchFailedError

    def _boom(*_args: object, **_kwargs: object) -> object:
        raise FetchFailedError("https://example.gov/x", "connection refused")

    monkeypatch.setattr(services, "preview_recipe", _boom)
    resp = client.post(PREVIEW_PATH, json={"recipe_yaml": INLINE_RECIPE, "url": "https://x"})
    assert resp.status_code == 502
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["title"] == "Upstream fetch failed"


def test_preview_staff_gate_token_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("RECIPE_PREVIEW_STAFF_TOKEN", "s3cr3t")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            # No token header -> 403.
            resp = test_client.post(
                PREVIEW_PATH, json={"recipe_yaml": INLINE_RECIPE, "html": GOOD_HTML}
            )
            assert resp.status_code == 403
            assert resp.headers["content-type"].startswith("application/problem+json")
            # Correct token -> allowed.
            ok = test_client.post(
                PREVIEW_PATH,
                json={"recipe_yaml": INLINE_RECIPE, "html": GOOD_HTML},
                headers={"X-Staff-Token": "s3cr3t"},
            )
            assert ok.status_code == 200
    finally:
        get_settings.cache_clear()


def test_preview_fail_closed_without_token_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("RECIPE_PREVIEW_STAFF_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as test_client:
            resp = test_client.post(
                PREVIEW_PATH, json={"recipe_yaml": INLINE_RECIPE, "html": GOOD_HTML}
            )
            assert resp.status_code == 403
    finally:
        get_settings.cache_clear()
