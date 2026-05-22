"""Meeting/agenda platform connectors: ``boarddocs`` / ``granicus_peak`` /
``civicplus`` (doc 18 §1 cat. E, §5 wave 2 #6-#8; D7).

All three share the :class:`MeetingPlatformConnector` base: ``discover`` crawls a
tenant's meeting index for per-meeting detail links; the runner fetches + extracts
each detail page (static HTML). Covered here against a mocked httpx transport (no
real network): tenant-URL construction from per-tenant config, listing-link
parsing (primary + fallback selectors), absolute-URL resolution + de-dup, the full
discover→fetch→extract→normalize, and config validation.
"""

from __future__ import annotations

import unittest.mock as mock
from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors.base import ConnectorError
from civicsignals_api.modules.ingestion.connectors.boarddocs import (
    BoarddocsConfig,
    BoarddocsConnector,
)
from civicsignals_api.modules.ingestion.connectors.civicplus import CivicplusConnector
from civicsignals_api.modules.ingestion.connectors.granicus_peak import GranicusPeakConnector
from civicsignals_api.modules.ingestion.connectors.http_static import HttpxFetcher
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import Recipe, SourcePointer

Handler = Callable[[httpx.Request], httpx.Response]


def _recipe(connector: str, connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": f"{connector}-test",
            "connector": connector,
            "version": 1,
            "entity": {"name": "Example", "state": "WA"},
            # Disable robots so the mocked transport doesn't need a robots.txt.
            "fetch": {"respect_robots_txt": False, "politeness_seconds": 0},
            "connector_config": {connector: connector_config},
            "fields": {"title": {"selectors": ["h1.meeting-title", "h1"], "required": True}},
            "signal_types": ["board_decision"],
        }
    )


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# discover: listing -> detail-page pointers
# ---------------------------------------------------------------------------


def test_boarddocs_builds_tenant_url_and_parses_links() -> None:
    listing = (
        "<html><body><ul>"
        "<li class='meeting'><a class='meeting-link' href='/wa/example-sd/Agenda/123'>May</a></li>"
        "<li class='meeting'><a class='meeting-link' href='/wa/example-sd/Agenda/124'>Apr</a></li>"
        "</ul></body></html>"
    )
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, text=listing)

    connector = BoarddocsConnector(_recipe("boarddocs", {"state": "wa", "subdomain": "example-sd"}))
    with mock.patch.object(HttpxFetcher, "_get_client", lambda self: _client(handler)):
        pointers = connector.discover([])

    # The listing URL is the per-tenant pattern.
    assert seen_urls[0] == "https://go.boarddocs.com/wa/example-sd/Board.nsf/Public"
    assert {p.url for p in pointers} == {
        "https://go.boarddocs.com/wa/example-sd/Agenda/123",
        "https://go.boarddocs.com/wa/example-sd/Agenda/124",
    }
    assert all(isinstance(p, SourcePointer) for p in pointers)


def test_fallback_selector_used_when_primary_misses() -> None:
    # No `a.meeting-link`; only the generic agenda-link fallback matches.
    listing = "<html><body><a href='/x/Agenda/1'>m1</a></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=listing)

    connector = BoarddocsConnector(
        _recipe("boarddocs", {"base_url": "https://go.boarddocs.com/x/y"})
    )
    with mock.patch.object(HttpxFetcher, "_get_client", lambda self: _client(handler)):
        pointers = connector.discover([])
    assert [p.url for p in pointers] == ["https://go.boarddocs.com/x/Agenda/1"]


def test_links_deduplicated() -> None:
    listing = (
        "<html><body>"
        "<a class='meeting-link' href='/a/Agenda/1'>x</a>"
        "<a class='meeting-link' href='/a/Agenda/1'>x dup</a>"
        "</body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=listing)

    connector = BoarddocsConnector(
        _recipe("boarddocs", {"base_url": "https://go.boarddocs.com/a/b"})
    )
    with mock.patch.object(HttpxFetcher, "_get_client", lambda self: _client(handler)):
        pointers = connector.discover([])
    assert len(pointers) == 1


def test_granicus_builds_view_publisher_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<html><body></body></html>")

    connector = GranicusPeakConnector(
        _recipe("granicus_peak", {"subdomain": "example-city", "view_id": 3})
    )
    with mock.patch.object(HttpxFetcher, "_get_client", lambda self: _client(handler)):
        connector.discover([])
    assert seen[0] == "https://example-city.granicus.com/ViewPublisher.php?view_id=3"


def test_civicplus_builds_platform_host() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<html><body></body></html>")

    connector = CivicplusConnector(
        _recipe("civicplus", {"subdomain": "example-town", "platform": "civicweb"})
    )
    with mock.patch.object(HttpxFetcher, "_get_client", lambda self: _client(handler)):
        connector.discover([])
    assert seen[0] == "https://example-town.civicweb.net/Portal/MeetingTypeList.aspx"


# ---------------------------------------------------------------------------
# full lifecycle: listing -> detail fetch -> extract -> normalize
# ---------------------------------------------------------------------------


def test_full_lifecycle_through_ingestion_service() -> None:
    listing_url = "https://go.boarddocs.com/wa/example-sd/Board.nsf/Public"
    detail_url = "https://go.boarddocs.com/wa/example-sd/Agenda/200"
    detail_html = "<html><body><h1 class='meeting-title'>May Board Meeting</h1></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == listing_url:
            return httpx.Response(200, text=f"<a class='meeting-link' href='{detail_url}'>m</a>")
        if url == detail_url:
            return httpx.Response(200, text=detail_html)
        if url.endswith("/robots.txt"):  # pragma: no cover - robots disabled
            return httpx.Response(404)
        return httpx.Response(404)

    recipe = _recipe("boarddocs", {"state": "wa", "subdomain": "example-sd"})

    with (
        mock.patch.object(HttpxFetcher, "_get_client", lambda self: _client(handler)),
        mock.patch.object(ingestion_services, "load_recipe", lambda rid: recipe),
    ):
        records = ingestion_services.crawl_recipe_with_connector("boarddocs-test", [])

    assert len(records) == 1
    assert records[0].fields["title"] == "May Board Meeting"
    assert records[0].source_url == detail_url
    assert records[0].signal_types == ["board_decision"]


def test_seed_urls_override_listing() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="<html><body></body></html>")

    connector = BoarddocsConnector(_recipe("boarddocs", {"state": "wa", "subdomain": "x"}))
    with mock.patch.object(HttpxFetcher, "_get_client", lambda self: _client(handler)):
        connector.discover(["https://go.boarddocs.com/wa/x/Custom/Index"])
    assert seen[0] == "https://go.boarddocs.com/wa/x/Custom/Index"


# ---------------------------------------------------------------------------
# config validation
# ---------------------------------------------------------------------------


def test_boarddocs_requires_base_url_or_state_subdomain() -> None:
    connector = BoarddocsConnector(_recipe("boarddocs", {}))
    with pytest.raises(ConnectorError, match="base_url or state\\+subdomain"):
        connector.discover([])


def test_boarddocs_config_defaults() -> None:
    cfg = BoarddocsConfig(state="wa", subdomain="x")
    assert cfg.meeting_path == "Board.nsf/Public"
