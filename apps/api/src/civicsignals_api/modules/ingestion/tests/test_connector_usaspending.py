"""``usaspending`` connector (doc 18 §1 cat. A, §5 wave 3 #14; D8).

Covers the award-search ``results`` envelope, 1-based ``page`` paging advanced via
``page_metadata.hasNext``, query-param passthrough, the full
discover→fetch→extract→normalize through the ingestion service, and config
validation — all against a mocked httpx transport (no real network).
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx

from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors.rest_api_pager import RestApiPagerFetcher
from civicsignals_api.modules.ingestion.connectors.usaspending import (
    UsaspendingConfig,
    UsaspendingConnector,
)
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import Recipe

Handler = Callable[[httpx.Request], httpx.Response]


def _recipe(connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "usaspending-test",
            "connector": "usaspending",
            "version": 3,
            "entity": {"name": "Dept of Education", "state": "DC"},
            "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
            "connector_config": {"usaspending": connector_config},
            "fields": {
                "title": {"selectors": ["dd[data-key='description']"], "required": True},
            },
            "signal_types": ["contract_award"],
        }
    )


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _discover_with_handler(connector: UsaspendingConnector, handler: Handler) -> list[object]:
    with mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)):
        return list(connector.discover([]))


def _envelope(results: list[dict[str, object]], *, has_next: bool) -> dict[str, object]:
    return {"results": results, "page_metadata": {"hasNext": has_next}}


def test_paginates_results_via_has_next() -> None:
    pages = {
        "1": _envelope([{"generated_internal_id": "A", "description": "Award A"}], has_next=True),
        "2": _envelope([{"generated_internal_id": "B", "description": "Award B"}], has_next=False),
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page") or "1"
        seen.append(page)
        return httpx.Response(200, json=pages[page])

    connector = UsaspendingConnector(_recipe({"page_size": 1, "max_pages": 5}))
    pointers = _discover_with_handler(connector, handler)
    assert len(pointers) == 2
    assert seen == ["1", "2"]  # 1-based; stopped after hasNext: false


def test_query_params_passed_through() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["award_type_codes"] = request.url.params.get("award_type_codes") or ""
        return httpx.Response(200, json=_envelope([], has_next=False))

    connector = UsaspendingConnector(_recipe({"query_params": {"award_type_codes": "A,B,C"}}))
    _discover_with_handler(connector, handler)
    assert seen["award_type_codes"] == "A,B,C"


def test_short_page_stops_without_has_next() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # A short page (1 < page_size 50) with no hasNext signal -> last page.
        return httpx.Response(
            200, json={"results": [{"generated_internal_id": "X", "description": "Only"}]}
        )

    connector = UsaspendingConnector(_recipe({"max_pages": 5}))
    pointers = _discover_with_handler(connector, handler)
    assert len(pointers) == 1


def test_full_lifecycle_through_ingestion_service() -> None:
    pages = {
        "1": _envelope(
            [
                {
                    "generated_internal_id": "CONT_1",
                    "description": "School modernization",
                    "recipient_name": "Acme LLC",
                }
            ],
            has_next=False,
        )
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params.get("page") or "1"])

    recipe = _recipe({"page_size": 50})

    with (
        mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)),
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        records = ingestion_services.crawl_recipe_with_connector("usaspending-test", [])

    assert len(records) == 1
    assert records[0].fields["title"] == "School modernization"
    assert records[0].recipe_version == 3


def test_config_model_defaults() -> None:
    cfg = UsaspendingConfig()
    assert cfg.base_url == "https://api.usaspending.gov"
    assert cfg.endpoint == "/api/v2/search/spending_by_award/"
    assert cfg.page_size == 100
    assert cfg.query_params is None
