"""Tests for the authoring HttpxFetcher (TODO D5).

Uses httpx's MockTransport so the live-fetch path is exercised without real
network access. Covers a successful fetch, a missing robots.txt (permissive),
a present robots.txt, and a transport error surfaced as a RecipeError.
"""

from __future__ import annotations

import httpx
import pytest

from civicsignals_api.modules.recipes.http_fetcher import HttpxFetcher
from civicsignals_api.modules.recipes.runner import RecipeError

UA = "CivicSignalsBot/1.0"


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_fetch_returns_status_body_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<h1>hi</h1>", headers={"X-Test": "1"})

    with HttpxFetcher(client=_client(handler)) as fetcher:
        status, body, headers = fetcher.fetch(
            "https://example.gov/x", user_agent=UA, max_redirects=5
        )
    assert status == 200
    assert "<h1>hi</h1>" in body
    assert headers["x-test"] == "1"


def test_robots_txt_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/robots.txt"
        return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")

    with HttpxFetcher(client=_client(handler)) as fetcher:
        body = fetcher.robots_txt("https://example.gov/some/page", user_agent=UA)
    assert body is not None
    assert "Disallow: /private/" in body


def test_robots_txt_absent_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with HttpxFetcher(client=_client(handler)) as fetcher:
        assert fetcher.robots_txt("https://example.gov/x", user_agent=UA) is None


def test_robots_txt_transport_error_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with HttpxFetcher(client=_client(handler)) as fetcher:
        assert fetcher.robots_txt("https://example.gov/x", user_agent=UA) is None


def test_fetch_transport_error_raises_recipe_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with HttpxFetcher(client=_client(handler)) as fetcher, pytest.raises(RecipeError):
        fetcher.fetch("https://example.gov/x", user_agent=UA, max_redirects=5)
