"""``rss`` connector (doc 18 §1 cat. B, §5 wave 1 #2; D6).

feedparser ships in the ``ingestion`` optional extra (not installed in CI's
``--frozen`` test job), so the feed-parsing tests ``importorskip`` it; the config,
discover-wiring, and missing-dependency guard are tested without the extra. The
feed HTTP fetch is mocked (no real network).
"""

from __future__ import annotations

import pytest

from civicsignals_api.modules.ingestion.connectors import rss as rss_module
from civicsignals_api.modules.ingestion.connectors.base import ConnectorError
from civicsignals_api.modules.ingestion.connectors.http_static import HttpxFetcher
from civicsignals_api.modules.ingestion.connectors.rss import (
    RssConfig,
    RssConnector,
    RssUnavailableError,
)
from civicsignals_api.modules.recipes import services as recipes_services

_FEED_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>GovTech Feed</title>
  <item>
    <title>City adopts new procurement portal</title>
    <link>https://govtech.example/articles/portal</link>
    <pubDate>Tue, 20 May 2025 09:00:00 GMT</pubDate>
    <description>A summary of the article.</description>
  </item>
  <item>
    <title>State releases RFP for fiber</title>
    <link>https://govtech.example/articles/fiber</link>
    <pubDate>Mon, 19 May 2025 09:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

_RECIPE: dict[str, object] = {
    "recipe_id": "govtech-feed",
    "connector": "rss",
    "version": 1,
    "entity": {"name": "GovTech", "kind": "publisher"},
    "connector_config": {"rss": {"feed_url": "https://govtech.example/rss"}},
    "fields": {"title": {"selectors": ["h1"]}},
    "signal_types": ["news"],
}


def _connector(**cfg_overrides: object) -> RssConnector:
    cc = {"rss": {"feed_url": "https://govtech.example/rss", **cfg_overrides}}
    recipe = recipes_services.parse_recipe({**_RECIPE, "connector_config": cc})
    return RssConnector(recipe)


def _patch_feed_fetch(
    monkeypatch: pytest.MonkeyPatch, *, body: str, status: int = 200
) -> dict[str, str]:
    """Patch ``HttpxFetcher.fetch`` to serve a canned feed body; records the URL.

    The RSS connector constructs its own :class:`HttpxFetcher` inside ``discover``,
    so we stub the fetch method rather than inject a client (no real network).
    """
    seen: dict[str, str] = {}

    def fake_fetch(
        self: HttpxFetcher, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        seen["url"] = url
        return status, body, {"content-type": "application/rss+xml"}

    monkeypatch.setattr(HttpxFetcher, "fetch", fake_fetch)
    monkeypatch.setattr(HttpxFetcher, "close", lambda self: None)
    return seen


def test_config_parses_defaults() -> None:
    connector = _connector()
    assert isinstance(connector.config, RssConfig)
    assert connector.config.feed_url == "https://govtech.example/rss"
    assert connector.config.fetch_item_body is True


def test_discover_parses_feed_into_item_pointers(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("feedparser")
    _patch_feed_fetch(monkeypatch, body=_FEED_XML)

    connector = _connector()
    pointers = connector.discover([])
    assert [p.url for p in pointers] == [
        "https://govtech.example/articles/portal",
        "https://govtech.example/articles/fiber",
    ]
    # Feed-level fields are carried as hint_metadata (fallback if the article 404s).
    assert pointers[0].hint_metadata["title"] == "City adopts new procurement portal"
    assert "summary" in pointers[0].hint_metadata
    assert all(p.connector == "rss" for p in pointers)


def test_discover_respects_max_items(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("feedparser")
    _patch_feed_fetch(monkeypatch, body=_FEED_XML)

    connector = _connector(max_items=1)
    pointers = connector.discover([])
    assert len(pointers) == 1
    assert pointers[0].url == "https://govtech.example/articles/portal"


def test_discover_feed_404_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 404 feed must surface (recipe paused, not silently empty — doc 18 §4 cat. B).
    _patch_feed_fetch(monkeypatch, body="", status=404)

    connector = _connector()
    with pytest.raises(ConnectorError, match="404"):
        connector.discover([])


def test_no_feed_url_and_no_seed_raises() -> None:
    recipe = recipes_services.parse_recipe({**_RECIPE, "connector_config": {"rss": {}}})
    connector = RssConnector(recipe)
    with pytest.raises(ConnectorError, match="no feed_url"):
        connector.discover([])


def test_feed_url_falls_back_to_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("feedparser")
    seen = _patch_feed_fetch(monkeypatch, body=_FEED_XML)

    recipe = recipes_services.parse_recipe({**_RECIPE, "connector_config": {"rss": {}}})
    connector = RssConnector(recipe)
    connector.discover(["https://other.example/feed.xml"])
    assert seen["url"] == "https://other.example/feed.xml"


def test_missing_feedparser_raises_clear_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate the extra not being installed: _parse_feed must raise the guard.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "feedparser":
            raise ImportError("No module named 'feedparser'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RssUnavailableError):
        rss_module._parse_feed("<rss></rss>")


def test_build_fetcher_returns_httpx_fetcher() -> None:
    connector = _connector()
    fetcher = connector.build_fetcher()
    assert isinstance(fetcher, HttpxFetcher)
    fetcher.close()
