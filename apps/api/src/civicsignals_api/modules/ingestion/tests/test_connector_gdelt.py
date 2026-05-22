"""``gdelt`` connector (doc 18 §1 cat. A, §5 wave 3 #15; D8).

Covers the single (unpaginated) DOC query, the ``articles`` envelope, the
``query``/``timespan``/``maxrecords`` params, article-URL pointer identity, the
full discover→fetch→extract→normalize, and config validation — all against a
mocked httpx transport (no real network).
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors.gdelt import GdeltConfig, GdeltConnector
from civicsignals_api.modules.ingestion.connectors.rest_api_pager import RestApiPagerFetcher
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.runner import RecipeValidationError
from civicsignals_api.modules.recipes.services import Recipe

Handler = Callable[[httpx.Request], httpx.Response]


def _recipe(connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "gdelt-test",
            "connector": "gdelt",
            "version": 1,
            "entity": {"name": "Austin ISD", "state": "TX"},
            "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
            "connector_config": {"gdelt": connector_config},
            "fields": {
                "title": {"selectors": ["dd[data-key='title']"], "required": True},
                "source_url": {"selectors": ["dd[data-key='url']"], "required": True},
            },
            "signal_types": ["news_mention"],
        }
    )


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _discover_with_handler(connector: GdeltConnector, handler: Handler) -> list[object]:
    with mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)):
        return list(connector.discover([]))


def test_single_query_emits_one_pointer_per_article() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params.get("query") or "")
        assert request.url.params.get("mode") == "ArtList"
        assert request.url.params.get("format") == "json"
        assert request.url.params.get("maxrecords") == "75"
        assert request.url.params.get("timespan") == "1d"
        return httpx.Response(
            200,
            json={
                "articles": [
                    {"url": "https://news.example/a", "title": "Article A"},
                    {"url": "https://news.example/b", "title": "Article B"},
                ]
            },
        )

    connector = GdeltConnector(_recipe({"query": '"Austin ISD"'}))
    pointers = _discover_with_handler(connector, handler)
    assert len(calls) == 1  # GDELT is unpaginated -> exactly one request
    assert len(pointers) == 2
    # The pointer URL is the article's own URL (stable identity).
    assert {p.url for p in pointers} == {  # type: ignore[attr-defined]
        "https://news.example/a",
        "https://news.example/b",
    }


def test_empty_articles_yields_no_pointers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"articles": []})

    connector = GdeltConnector(_recipe({"query": "nothing"}))
    assert _discover_with_handler(connector, handler) == []


def test_full_lifecycle_through_ingestion_service() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "url": "https://news.example/austin",
                        "title": "Austin ISD names superintendent",
                        "domain": "news.example",
                    }
                ]
            },
        )

    recipe = _recipe({"query": '"Austin ISD"', "timespan": "3d"})

    with (
        mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)),
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        records = ingestion_services.crawl_recipe_with_connector("gdelt-test", [])

    assert len(records) == 1
    assert records[0].fields["title"] == "Austin ISD names superintendent"
    assert records[0].source_url == "https://news.example/austin"


def test_config_requires_query() -> None:
    with pytest.raises(RecipeValidationError):
        _recipe({"timespan": "1d"})  # missing required query


def test_config_model_defaults() -> None:
    cfg = GdeltConfig(query="x")
    assert cfg.base_url.endswith("/api/v2/doc/doc")
    assert cfg.timespan == "1d"
    assert cfg.max_records == 75
