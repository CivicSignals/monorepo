"""``ckan`` connector (doc 18 §1 cat. E, §5 wave 2 #10; D7).

Covers the CKAN Action API envelope (``result.records``), ``limit``/``offset``
paging, the ``Authorization`` API-key header, ``q``/``filters`` passthrough, the
``success: false`` error surface, the full discover→fetch→extract→normalize, and
config validation — all against a mocked httpx transport (no real network).
"""

from __future__ import annotations

import json
import unittest.mock as mock
from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors.base import ConnectorError
from civicsignals_api.modules.ingestion.connectors.ckan import CkanConfig, CkanConnector
from civicsignals_api.modules.ingestion.connectors.rest_api_pager import RestApiPagerFetcher
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.runner import RecipeValidationError
from civicsignals_api.modules.recipes.services import Recipe

Handler = Callable[[httpx.Request], httpx.Response]


def _recipe(connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "ckan-test",
            "connector": "ckan",
            "version": 2,
            "entity": {"name": "WA Commerce", "state": "WA"},
            "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
            "connector_config": {"ckan": connector_config},
            "fields": {
                "title": {"selectors": ["dd[data-key='title']"], "required": True},
            },
            "signal_types": ["budget"],
        }
    )


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _discover_with_handler(connector: CkanConnector, handler: Handler) -> list[object]:
    with mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)):
        return list(connector.discover([]))


def _envelope(records: list[dict[str, object]]) -> dict[str, object]:
    return {"success": True, "result": {"records": records, "total": len(records)}}


def test_paginates_records_under_result() -> None:
    pages = {
        0: _envelope([{"_id": 1, "title": "A"}, {"_id": 2, "title": "B"}]),
        2: _envelope([{"_id": 3, "title": "C"}]),  # short page -> last
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset") or "0"
        seen.append(offset)
        assert request.url.params.get("resource_id") == "res-1"
        return httpx.Response(200, json=pages[int(offset)])

    connector = CkanConnector(
        _recipe({"domain": "data.wa.gov", "resource_id": "res-1", "page_size": 2})
    )
    pointers = _discover_with_handler(connector, handler)
    assert len(pointers) == 3
    assert seen == ["0", "2"]


def test_api_key_header_and_filters() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        seen["q"] = request.url.params.get("q") or ""
        seen["filters"] = request.url.params.get("filters") or ""
        return httpx.Response(200, json=_envelope([]))

    connector = CkanConnector(
        _recipe(
            {
                "domain": "data.wa.gov",
                "resource_id": "res-1",
                "q": "broadband",
                "filters": {"status": "open"},
                "api_key": "key-abc",
            }
        )
    )
    _discover_with_handler(connector, handler)
    assert seen["auth"] == "key-abc"
    assert seen["q"] == "broadband"
    assert json.loads(seen["filters"]) == {"status": "open"}


def test_success_false_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "error": {"message": "Not found"}})

    connector = CkanConnector(_recipe({"domain": "data.wa.gov", "resource_id": "missing"}))
    with pytest.raises(ConnectorError, match="datastore_search error"):
        _discover_with_handler(connector, handler)


def test_full_lifecycle_through_ingestion_service() -> None:
    pages = {0: _envelope([{"_id": 1, "title": "Grant A"}]), 1: _envelope([])}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[int(request.url.params.get("offset") or "0")])

    recipe = _recipe({"domain": "data.wa.gov", "resource_id": "res-1", "page_size": 50})

    with (
        mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)),
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        records = ingestion_services.crawl_recipe_with_connector("ckan-test", [])

    assert len(records) == 1
    assert records[0].fields["title"] == "Grant A"
    assert records[0].recipe_version == 2


def test_config_requires_domain_and_resource() -> None:
    with pytest.raises(RecipeValidationError):
        _recipe({"domain": "data.wa.gov"})  # missing resource_id


def test_config_model_defaults() -> None:
    cfg = CkanConfig(domain="data.wa.gov", resource_id="res-1")
    assert cfg.page_size == 100
    assert cfg.api_key is None
