"""``arcgis_rest`` connector (doc 18 §1 cat. E, §5 wave 2 #11; D7).

Covers the ArcGIS query envelope (``features[].attributes``), ``resultOffset`` /
``exceededTransferLimit`` paging, the ``f=json`` + ``where`` params, token-in-query
auth, the ``error`` surface, geometry opt-in, the full
discover→fetch→extract→normalize, and config validation — all against a mocked
httpx transport (no real network).
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors.arcgis_rest import (
    ArcgisRestConfig,
    ArcgisRestConnector,
)
from civicsignals_api.modules.ingestion.connectors.base import ConnectorError
from civicsignals_api.modules.ingestion.connectors.rest_api_pager import RestApiPagerFetcher
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.runner import RecipeValidationError
from civicsignals_api.modules.recipes.services import Recipe

Handler = Callable[[httpx.Request], httpx.Response]

_LAYER = "https://services.arcgis.com/x/arcgis/rest/services/Cap/FeatureServer/0"


def _recipe(connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "arcgis-test",
            "connector": "arcgis_rest",
            "version": 1,
            "entity": {"name": "City", "state": "CO"},
            "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
            "connector_config": {"arcgis_rest": connector_config},
            "fields": {
                "title": {"selectors": ["dd[data-key='PROJECT_NAME']"], "required": True},
            },
            "signal_types": ["budget"],
        }
    )


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _discover_with_handler(connector: ArcgisRestConnector, handler: Handler) -> list[object]:
    with mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)):
        return list(connector.discover([]))


def _features(*names: str) -> dict[str, object]:
    return {
        "features": [
            {"attributes": {"OBJECTID": i, "PROJECT_NAME": n}, "geometry": {"x": 1, "y": 2}}
            for i, n in enumerate(names, start=1)
        ]
    }


def test_paginates_with_exceeded_transfer_limit() -> None:
    pages = {
        0: {**_features("A", "B"), "exceededTransferLimit": True},
        2: {**_features("C"), "exceededTransferLimit": False},
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("resultOffset") or "0"
        seen.append(offset)
        assert request.url.params.get("f") == "json"
        assert request.url.params.get("where") == "1=1"
        return httpx.Response(200, json=pages[int(offset)])

    connector = ArcgisRestConnector(_recipe({"layer_url": _LAYER, "page_size": 2}))
    pointers = _discover_with_handler(connector, handler)
    assert len(pointers) == 3
    assert seen == ["0", "2"]


def test_token_in_query() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.url.params.get("token") or ""
        return httpx.Response(200, json={"features": []})

    connector = ArcgisRestConnector(_recipe({"layer_url": _LAYER, "token": "tk-99"}))
    _discover_with_handler(connector, handler)
    assert seen["token"] == "tk-99"


def test_error_object_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": {"code": 400, "message": "Invalid query"}})

    connector = ArcgisRestConnector(_recipe({"layer_url": _LAYER}))
    with pytest.raises(ConnectorError, match="query error"):
        _discover_with_handler(connector, handler)


def test_geometry_excluded_by_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("returnGeometry") == "false"
        if int(request.url.params.get("resultOffset") or "0") == 0:
            return httpx.Response(200, json=_features("A"))
        return httpx.Response(200, json={"features": []})

    recipe = _recipe({"layer_url": _LAYER, "page_size": 50})

    with (
        mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)),
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        records = ingestion_services.crawl_recipe_with_connector("arcgis-test", [])
    assert len(records) == 1
    # geometry was not projected into the record (no `dd[data-key='geometry']`).
    assert records[0].fields["title"] == "A"


def test_config_requires_layer_url() -> None:
    with pytest.raises(RecipeValidationError):
        _recipe({})  # missing layer_url


def test_config_model_defaults() -> None:
    cfg = ArcgisRestConfig(layer_url=_LAYER)
    assert cfg.where == "1=1"
    assert cfg.out_fields == "*"
    assert cfg.include_geometry is False
