"""``rest_api_pager`` connector (doc 18 §1 cat. A, §5 wave 1 #4; D6).

Covers cursor / offset / page pagination, api-key + bearer auth (incl. ``env:``
secret resolution), client-side rate limiting, and retry on 429/5xx — all against
a mocked httpx transport (no real network).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion.connectors.base import ConnectorError
from civicsignals_api.modules.ingestion.connectors.rest_api_pager import (
    AuthConfig,
    PaginationConfig,
    RestApiPagerConfig,
    RestApiPagerConnector,
    RestApiPagerFetcher,
)
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import Recipe, SourcePointer

Handler = Callable[[httpx.Request], httpx.Response]


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _recipe(connector_config: dict[str, object]) -> Recipe:
    return recipes_services.parse_recipe(
        {
            "recipe_id": "grants-api",
            "connector": "rest_api_pager",
            "version": 1,
            "entity": {"name": "Grants.gov"},
            "connector_config": {"rest_api_pager": connector_config},
            "fields": {"title": {"selectors": ["title"]}},
        }
    )


def _connector(
    connector_config: dict[str, object], *, clock: FakeClock | None = None
) -> RestApiPagerConnector:
    return RestApiPagerConnector(_recipe(connector_config), clock=clock)


def _client(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def _discover_with_handler(
    connector: RestApiPagerConnector, handler: Handler
) -> list[SourcePointer]:
    """Run ``connector.discover`` with the fetcher's client mocked.

    ``discover`` constructs its own :class:`RestApiPagerFetcher`; patching
    ``_get_client`` (the single place the client is built) injects the mocked
    transport without touching the constructor.
    """
    import unittest.mock as mock

    with mock.patch.object(RestApiPagerFetcher, "_get_client", lambda self: _client(handler)):
        return list(connector.discover([]))


def test_page_mode_walks_until_empty() -> None:
    pages = {
        1: {"items": [{"id": 1}, {"id": 2}]},
        2: {"items": [{"id": 3}]},
        3: {"items": []},
    }
    seen_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page")
        seen_pages.append(page or "")
        return httpx.Response(200, json=pages[int(page or "1")])

    connector = _connector(
        {
            "base_url": "https://api.grants.gov/v1/opportunities",
            "pagination": {"mode": "page", "page_size": 2, "start_page": 1, "max_pages": 10},
        }
    )
    pointers = _discover_with_handler(connector, handler)
    # Pages 1 and 2 yielded items; page 3 was empty -> stop.
    assert len(pointers) == 2
    assert seen_pages == ["1", "2", "3"]
    assert pointers[0].hint_metadata["item_count"] == 2
    assert pointers[1].hint_metadata["item_count"] == 1


def test_offset_mode_increments_offset() -> None:
    by_offset = {0: {"items": [{"id": 1}]}, 10: {"items": []}}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset")
        seen.append(offset or "")
        return httpx.Response(200, json=by_offset[int(offset or "0")])

    connector = _connector(
        {
            "base_url": "https://api.example/records",
            "pagination": {"mode": "offset", "page_size": 10, "max_pages": 10},
        }
    )
    pointers = _discover_with_handler(connector, handler)
    assert len(pointers) == 1
    assert seen == ["0", "10"]


def test_cursor_mode_follows_next_cursor() -> None:
    responses = {
        None: {"data": {"items": [{"id": 1}]}, "meta": {"next": "CUR2"}},
        "CUR2": {"data": {"items": [{"id": 2}]}, "meta": {"next": None}},
    }
    seen_cursors: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cur = request.url.params.get("cursor")
        seen_cursors.append(cur)
        return httpx.Response(200, json=responses[cur])

    connector = _connector(
        {
            "base_url": "https://api.example/feed",
            "pagination": {
                "mode": "cursor",
                "items_path": "data.items",
                "cursor_path": "meta.next",
                "max_pages": 10,
            },
        }
    )
    pointers = _discover_with_handler(connector, handler)
    assert len(pointers) == 2
    assert seen_cursors == [None, "CUR2"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_bearer_auth_header_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"items": []})

    config = RestApiPagerConfig(
        base_url="https://api.example/x",
        pagination=PaginationConfig(mode="page"),
        auth=AuthConfig(mode="bearer", value="secret-token"),
    )
    fetcher = RestApiPagerFetcher(config, client=_client(handler))
    fetcher.fetch("https://api.example/x", user_agent="UA", max_redirects=5)
    assert seen["auth"] == "Bearer secret-token"


def test_api_key_header_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key", "")
        return httpx.Response(200, json={"items": []})

    config = RestApiPagerConfig(
        base_url="https://api.example/x",
        pagination=PaginationConfig(mode="page"),
        auth=AuthConfig(mode="api_key", header="X-Api-Key", value="abc123"),
    )
    fetcher = RestApiPagerFetcher(config, client=_client(handler))
    fetcher.fetch("https://api.example/x", user_agent="UA", max_redirects=5)
    assert seen["key"] == "abc123"


def test_api_key_query_param() -> None:
    config = RestApiPagerConfig(
        base_url="https://api.example/x",
        pagination=PaginationConfig(mode="page"),
        auth=AuthConfig(mode="api_key", param="api_key", value="qkey"),
    )
    fetcher = RestApiPagerFetcher(config)
    assert fetcher.auth_query() == {"api_key": "qkey"}


def test_env_secret_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRANTS_API_KEY", "from-env")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"items": []})

    config = RestApiPagerConfig(
        base_url="https://api.example/x",
        pagination=PaginationConfig(mode="page"),
        auth=AuthConfig(mode="bearer", value="env:GRANTS_API_KEY"),
    )
    fetcher = RestApiPagerFetcher(config, client=_client(handler))
    fetcher.fetch("https://api.example/x", user_agent="UA", max_redirects=5)
    assert seen["auth"] == "Bearer from-env"


def test_missing_env_secret_raises() -> None:
    config = RestApiPagerConfig(
        base_url="https://api.example/x",
        pagination=PaginationConfig(mode="page"),
        auth=AuthConfig(mode="bearer", value="env:DEFINITELY_NOT_SET_XYZ"),
    )
    with pytest.raises(ConnectorError, match="env var"):
        RestApiPagerFetcher(config)


# ---------------------------------------------------------------------------
# Rate limiting + retries
# ---------------------------------------------------------------------------


def test_rate_limit_spaces_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    clock = FakeClock()
    config = RestApiPagerConfig(
        base_url="https://api.example/x",
        pagination=PaginationConfig(mode="page"),
        rate_limit_per_minute=60,  # -> 1s min interval
    )
    fetcher = RestApiPagerFetcher(config, client=_client(handler), clock=clock)
    fetcher.fetch("https://api.example/x?page=1", user_agent="UA", max_redirects=5)
    fetcher.fetch("https://api.example/x?page=2", user_agent="UA", max_redirects=5)
    # First request: no wait. Second: waits the full 1s interval (clock didn't move).
    assert clock.slept == [1.0]


def test_retry_on_429_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json={"items": []})

    clock = FakeClock()
    config = RestApiPagerConfig(
        base_url="https://api.example/x", pagination=PaginationConfig(mode="page")
    )
    fetcher = RestApiPagerFetcher(config, client=_client(handler), clock=clock)
    status, _body, _ = fetcher.fetch("https://api.example/x", user_agent="UA", max_redirects=5)
    assert status == 200
    assert calls["n"] == 2


def test_auth_failure_in_discover_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    connector = _connector(
        {"base_url": "https://api.example/x", "pagination": {"mode": "page", "max_pages": 5}}
    )
    with pytest.raises(ConnectorError, match="auth failed"):
        _discover_with_handler(connector, handler)


def test_invalid_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json at all {{{")

    connector = _connector({"base_url": "https://api.example/x", "pagination": {"mode": "page"}})
    with pytest.raises(ConnectorError, match="not valid JSON"):
        _discover_with_handler(connector, handler)


def test_robots_txt_is_none_for_apis() -> None:
    config = RestApiPagerConfig(
        base_url="https://api.example/x", pagination=PaginationConfig(mode="page")
    )
    fetcher = RestApiPagerFetcher(config)
    assert fetcher.robots_txt("https://api.example/x", user_agent="UA") is None
