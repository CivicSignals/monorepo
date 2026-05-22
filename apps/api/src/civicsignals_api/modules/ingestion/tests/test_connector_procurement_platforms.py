"""``bonfire_euna`` + ``ionwave`` connectors (doc 18 §1 cat. E, §5 wave 3 #19-#20;
D8).

Both are multi-tenant e-procurement platforms ingested via their public listing
data APIs (offset paging, JSON→HTML projection). Covers paging, the
``items_path`` envelope, the public-only access guard (HTTP 403 → ConnectorError),
the full discover→fetch→extract→normalize, and config validation — all against a
mocked httpx transport (no real network).
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors.base import ConnectorError
from civicsignals_api.modules.ingestion.connectors.bonfire_euna import (
    BonfireEunaConfig,
    BonfireEunaConnector,
)
from civicsignals_api.modules.ingestion.connectors.ionwave import (
    IonwaveConfig,
    IonwaveConnector,
)
from civicsignals_api.modules.ingestion.connectors.rest_api_pager import RestApiPagerFetcher
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.runner import RecipeValidationError
from civicsignals_api.modules.recipes.services import Recipe

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# bonfire_euna
# ---------------------------------------------------------------------------


def _bonfire_recipe(connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "bonfire-test",
            "connector": "bonfire_euna",
            "version": 1,
            "entity": {"name": "City of Example", "state": "TX"},
            "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
            "connector_config": {"bonfire_euna": connector_config},
            "fields": {"title": {"selectors": ["dd[data-key='title']"], "required": True}},
            "signal_types": ["rfp_posted"],
        }
    )


def _bonfire_discover(connector: BonfireEunaConnector, handler: Handler) -> list[object]:
    with mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)):
        return list(connector.discover([]))


def test_bonfire_paginates_and_projects() -> None:
    pages = {
        "0": {
            "opportunities": [{"id": "OPP-1", "title": "RFP A"}, {"id": "OPP-2", "title": "RFP B"}]
        },
        "2": {"opportunities": [{"id": "OPP-3", "title": "RFP C"}]},  # short -> last
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset") or "0"
        seen.append(offset)
        return httpx.Response(200, json=pages[offset])

    connector = BonfireEunaConnector(
        _bonfire_recipe({"portal_url": "https://cityofx.bonfirehub.com", "page_size": 2})
    )
    pointers = _bonfire_discover(connector, handler)
    assert len(pointers) == 3
    assert seen == ["0", "2"]


def test_bonfire_login_gated_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    connector = BonfireEunaConnector(
        _bonfire_recipe({"portal_url": "https://locked.bonfirehub.com"})
    )
    with pytest.raises(ConnectorError, match="public listings only"):
        _bonfire_discover(connector, handler)


def test_bonfire_full_lifecycle() -> None:
    pages = {
        "0": {"opportunities": [{"id": "OPP-9", "title": "HVAC Replacement"}]},
        "50": {"opportunities": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params.get("offset") or "0"])

    recipe = _bonfire_recipe({"portal_url": "https://cityofx.bonfirehub.com"})
    with (
        mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)),
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        records = ingestion_services.crawl_recipe_with_connector("bonfire-test", [])
    assert len(records) == 1
    assert records[0].fields["title"] == "HVAC Replacement"


def test_bonfire_config_requires_portal_url() -> None:
    with pytest.raises(RecipeValidationError):
        _bonfire_recipe({"listing_path": "/x"})


def test_bonfire_config_defaults() -> None:
    cfg = BonfireEunaConfig(portal_url="https://x.bonfirehub.com")
    assert cfg.listing_path == "/api/portal/opportunities"
    assert cfg.items_path == "opportunities"
    assert cfg.page_size == 50


# ---------------------------------------------------------------------------
# ionwave
# ---------------------------------------------------------------------------


def _ionwave_recipe(connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "ionwave-test",
            "connector": "ionwave",
            "version": 1,
            "entity": {"name": "Example ISD", "state": "TX"},
            "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
            "connector_config": {"ionwave": connector_config},
            "fields": {"title": {"selectors": ["dd[data-key='title']"], "required": True}},
            "signal_types": ["rfp_posted"],
        }
    )


def _ionwave_discover(connector: IonwaveConnector, handler: Handler) -> list[object]:
    with mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)):
        return list(connector.discover([]))


def test_ionwave_paginates_and_projects() -> None:
    pages = {
        "0": {
            "bids": [
                {"bid_number": "B-1", "title": "Bid A"},
                {"bid_number": "B-2", "title": "Bid B"},
            ]
        },
        "2": {"bids": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params.get("offset") or "0"])

    connector = IonwaveConnector(
        _ionwave_recipe({"portal_url": "https://exampleisd.ionwave.net", "page_size": 2})
    )
    pointers = _ionwave_discover(connector, handler)
    assert len(pointers) == 2


def test_ionwave_login_gated_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    connector = IonwaveConnector(_ionwave_recipe({"portal_url": "https://locked.ionwave.net"}))
    with pytest.raises(ConnectorError, match="public listings only"):
        _ionwave_discover(connector, handler)


def test_ionwave_full_lifecycle() -> None:
    pages = {
        "0": {"bids": [{"bid_number": "B-44", "title": "Cafeteria Supply"}]},
        "50": {"bids": []},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages[request.url.params.get("offset") or "0"])

    recipe = _ionwave_recipe({"portal_url": "https://exampleisd.ionwave.net"})
    with (
        mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)),
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        records = ingestion_services.crawl_recipe_with_connector("ionwave-test", [])
    assert len(records) == 1
    assert records[0].fields["title"] == "Cafeteria Supply"


def test_ionwave_config_requires_portal_url() -> None:
    with pytest.raises(RecipeValidationError):
        _ionwave_recipe({"items_path": "bids"})


def test_ionwave_config_defaults() -> None:
    cfg = IonwaveConfig(portal_url="https://x.ionwave.net")
    assert cfg.listing_path == "/Bids/Public/List"
    assert cfg.items_path == "bids"
