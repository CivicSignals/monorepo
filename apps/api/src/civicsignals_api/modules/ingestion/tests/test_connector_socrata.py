"""``socrata`` connector (doc 18 §1 cat. E, §5 wave 2 #9; D7).

Covers SODA ``$limit``/``$offset`` paging, the ``X-App-Token`` header (incl.
``env:`` resolution), SoQL ``$where``/``$order`` passthrough, the JSON-row →
HTML ``<dl>`` projection the runner extracts over, full
discover→fetch→extract→normalize, and config validation — all against a mocked
httpx transport (no real network).
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors.base import ConnectorError
from civicsignals_api.modules.ingestion.connectors.rest_api_pager import RestApiPagerFetcher
from civicsignals_api.modules.ingestion.connectors.socrata import SocrataConfig, SocrataConnector
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.runner import RecipeValidationError
from civicsignals_api.modules.recipes.services import Recipe

Handler = Callable[[httpx.Request], httpx.Response]


def _recipe(connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "socrata-test",
            "connector": "socrata",
            "version": 3,
            "entity": {"name": "NYC", "state": "NY"},
            "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
            "connector_config": {"socrata": connector_config},
            "fields": {
                "title": {"selectors": ["dd[data-key='title']"], "required": True},
                "amount": {"selectors": ["dd[data-key='award_amount']"]},
            },
            "signal_types": ["contract_award"],
        }
    )


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _discover_with_handler(connector: SocrataConnector, handler: Handler) -> list[object]:
    with mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)):
        return list(connector.discover([]))


def test_paginates_with_limit_offset_and_stops_on_short_page() -> None:
    rows = {
        0: [{":id": "r1", "title": "A", "award_amount": "1"}, {":id": "r2", "title": "B"}],
        2: [{":id": "r3", "title": "C"}],  # short page -> last
    }
    seen_offsets: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("$offset") or "0"
        seen_offsets.append(offset)
        assert request.url.params.get("$limit") == "2"
        return httpx.Response(200, json=rows[int(offset)])

    connector = SocrataConnector(
        _recipe({"domain": "data.cityofnewyork.us", "dataset_id": "abcd-1234", "page_size": 2})
    )
    pointers = _discover_with_handler(connector, handler)
    assert len(pointers) == 3
    assert seen_offsets == ["0", "2"]


def test_app_token_header_and_soql_params() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("x-app-token", "")
        seen["where"] = request.url.params.get("$where") or ""
        seen["order"] = request.url.params.get("$order") or ""
        return httpx.Response(200, json=[])

    connector = SocrataConnector(
        _recipe(
            {
                "domain": "https://data.colorado.gov",
                "dataset_id": "wxyz-9999",
                "where": "amount > 1000",
                "order": ":id",
                "app_token": "tok-123",
            }
        )
    )
    _discover_with_handler(connector, handler)
    assert seen["token"] == "tok-123"
    assert seen["where"] == "amount > 1000"
    assert seen["order"] == ":id"


def test_env_app_token_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOCRATA_TOKEN", "from-env")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("x-app-token", "")
        return httpx.Response(200, json=[])

    connector = SocrataConnector(
        _recipe(
            {"domain": "data.x.gov", "dataset_id": "aaaa-bbbb", "app_token": "env:SOCRATA_TOKEN"}
        )
    )
    _discover_with_handler(connector, handler)
    assert seen["token"] == "from-env"


def test_auth_failure_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    connector = SocrataConnector(_recipe({"domain": "data.x.gov", "dataset_id": "aaaa-bbbb"}))
    with pytest.raises(ConnectorError, match="auth failed"):
        _discover_with_handler(connector, handler)


def test_full_lifecycle_through_ingestion_service() -> None:
    """discover→fetch→extract→normalize produces normalized records (D6 seam)."""
    rows = [
        {":id": "r1", "title": "Contract A", "award_amount": "5000"},
        {":id": "r2", "title": "Contract B", "award_amount": "9000"},
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # First page returns rows; second page empty.
        if int(request.url.params.get("$offset") or "0") == 0:
            return httpx.Response(200, json=rows)
        return httpx.Response(200, json=[])

    recipe = _recipe({"domain": "data.x.gov", "dataset_id": "aaaa-bbbb", "page_size": 50})

    def fake_load(recipe_id: str) -> Recipe:
        return recipe

    with (
        mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)),
        mock.patch.object(ingestion_services, "load_recipe", fake_load),
    ):
        records = ingestion_services.crawl_recipe_with_connector("socrata-test", [])

    assert len(records) == 2
    assert records[0].recipe_id == "socrata-test"
    assert records[0].recipe_version == 3
    assert records[0].fields["title"] == "Contract A"
    assert records[0].fields["amount"] == "5000"
    assert records[0].signal_types == ["contract_award"]


def test_config_requires_domain_and_dataset() -> None:
    with pytest.raises(RecipeValidationError):
        _recipe({"domain": "data.x.gov"})  # missing dataset_id


def test_config_model_defaults() -> None:
    cfg = SocrataConfig(domain="data.x.gov", dataset_id="aaaa-bbbb")
    assert cfg.page_size == 100
    assert cfg.max_pages == 50
    assert cfg.app_token is None
