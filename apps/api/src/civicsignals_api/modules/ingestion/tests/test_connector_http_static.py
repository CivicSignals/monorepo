"""``http_static`` connector + HttpxFetcher (doc 18 §1 cat. C, §5 wave 1 #1; D6).

Exercises the fetcher's retries/backoff + conditional GET against a mocked httpx
transport (no real network), and verifies the connector drives the runner so
robots.txt + the politeness window are honored (those live in the runner, applied
uniformly to every fetcher — doc 18 §2.2).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.connectors.http_static import (
    HttpStaticConfig,
    HttpStaticConnector,
    HttpxFetcher,
)
from civicsignals_api.modules.recipes import services as recipes_services

_HTML = '<html><body><h1 class="solicitation-title">RFP 1</h1></body></html>'

_RECIPE: dict[str, object] = {
    "recipe_id": "wa-state-webs",
    "connector": "http_static",
    "version": 1,
    "entity": {"name": "WA DES", "state": "WA"},
    "fetch": {"respect_robots_txt": True, "politeness_seconds": 10, "jitter_seconds": 0},
    "fields": {"title": {"selectors": ["h1.solicitation-title"], "required": True}},
    "signal_types": ["rfp_posted"],
}


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


Handler = Callable[[httpx.Request], httpx.Response]


def _fetcher_with_handler(handler: Handler, config: HttpStaticConfig | None = None) -> HttpxFetcher:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    return HttpxFetcher(config or HttpStaticConfig(backoff_seconds=0.0), client=client)


def test_fetch_returns_status_body_headers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML, headers={"ETag": '"abc"'})

    fetcher = _fetcher_with_handler(handler)
    status, body, headers = fetcher.fetch(
        "https://webs.des.wa.gov/rfp-1", user_agent="UA/1.0", max_redirects=5
    )
    assert status == 200
    assert "RFP 1" in body
    assert headers["etag"] == '"abc"'


def test_user_agent_is_sent() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text=_HTML)

    fetcher = _fetcher_with_handler(handler)
    fetcher.fetch("https://x.gov/a", user_agent="CivicSignalsBot/1.0", max_redirects=5)
    assert seen["ua"] == "CivicSignalsBot/1.0"


def test_retry_on_5xx_then_success() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text=_HTML)

    fetcher = _fetcher_with_handler(handler, HttpStaticConfig(max_attempts=3, backoff_seconds=0.0))
    status, _body, _ = fetcher.fetch("https://x.gov/a", user_agent="UA", max_redirects=5)
    assert status == 200
    assert calls["n"] == 3  # two 503s, third succeeds


def test_retry_exhausted_reraises_last_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    fetcher = _fetcher_with_handler(handler, HttpStaticConfig(max_attempts=2, backoff_seconds=0.0))
    with pytest.raises(httpx.HTTPStatusError):
        fetcher.fetch("https://x.gov/a", user_agent="UA", max_redirects=5)


def test_retry_on_network_error() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, text=_HTML)

    fetcher = _fetcher_with_handler(handler, HttpStaticConfig(max_attempts=3, backoff_seconds=0.0))
    status, _body, _ = fetcher.fetch("https://x.gov/a", user_agent="UA", max_redirects=5)
    assert status == 200
    assert calls["n"] == 2


def test_4xx_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, text="nope")

    fetcher = _fetcher_with_handler(handler, HttpStaticConfig(max_attempts=3, backoff_seconds=0.0))
    status, _body, _ = fetcher.fetch("https://x.gov/a", user_agent="UA", max_redirects=5)
    assert status == 404
    assert calls["n"] == 1  # 4xx is a definitive answer, not retried


def test_redirect_loop_capped_and_fails_visibly() -> None:
    # An endless redirect chain must fail rather than loop forever (doc 18 §4).
    def handler(request: httpx.Request) -> httpx.Response:
        # Always redirect to a new path -> exceeds max_redirects.
        n = int(request.url.params.get("n", "0"))
        return httpx.Response(302, headers={"Location": f"/loop?n={n + 1}"})

    fetcher = _fetcher_with_handler(handler, HttpStaticConfig(max_attempts=1, backoff_seconds=0.0))
    with pytest.raises(httpx.HTTPError):
        fetcher.fetch("https://x.gov/loop?n=0", user_agent="UA", max_redirects=3)


def test_conditional_get_sends_validators_and_handles_304() -> None:
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append({k.lower(): v for k, v in request.headers.items()})
        if "if-none-match" in {k.lower() for k in request.headers}:
            return httpx.Response(304)
        return httpx.Response(
            200, text=_HTML, headers={"ETag": '"v1"', "Last-Modified": "yesterday"}
        )

    fetcher = _fetcher_with_handler(
        handler, HttpStaticConfig(conditional_get=True, backoff_seconds=0.0)
    )
    url = "https://x.gov/listing"
    s1, b1, _ = fetcher.fetch(url, user_agent="UA", max_redirects=5)
    assert s1 == 200 and "RFP 1" in b1
    # Second fetch of the same URL sends the learned validators -> 304 (no change).
    s2, b2, _ = fetcher.fetch(url, user_agent="UA", max_redirects=5)
    assert s2 == 304
    assert b2 == ""  # 304 -> empty body, "no change"
    assert seen_headers[1]["if-none-match"] == '"v1"'
    assert seen_headers[1]["if-modified-since"] == "yesterday"


def test_conditional_get_disabled_sends_no_validators() -> None:
    seen_headers: list[set[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append({k.lower() for k in request.headers})
        return httpx.Response(200, text=_HTML, headers={"ETag": '"v1"'})

    fetcher = _fetcher_with_handler(
        handler, HttpStaticConfig(conditional_get=False, backoff_seconds=0.0)
    )
    url = "https://x.gov/a"
    fetcher.fetch(url, user_agent="UA", max_redirects=5)
    fetcher.fetch(url, user_agent="UA", max_redirects=5)
    assert "if-none-match" not in seen_headers[1]


def test_robots_txt_fetched_via_http() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text=_HTML)

    fetcher = _fetcher_with_handler(handler)
    robots = fetcher.robots_txt("https://x.gov/some/page", user_agent="UA")
    assert robots is not None
    assert "Disallow: /private/" in robots


def test_robots_txt_missing_is_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    fetcher = _fetcher_with_handler(handler)
    assert fetcher.robots_txt("https://x.gov/page", user_agent="UA") is None


# ---------------------------------------------------------------------------
# Connector dispatch end-to-end: robots + politeness enforced by the runner.
# ---------------------------------------------------------------------------


def test_connector_discover_is_pass_through() -> None:
    recipe = recipes_services.parse_recipe(_RECIPE)
    connector = HttpStaticConnector(recipe)
    pointers = connector.discover(["https://webs.des.wa.gov/a", "https://webs.des.wa.gov/b"])
    assert [p.url for p in pointers] == [
        "https://webs.des.wa.gov/a",
        "https://webs.des.wa.gov/b",
    ]
    assert all(p.connector == "http_static" for p in pointers)


def test_runner_enforces_robots_for_static_fetcher() -> None:
    # The connector's fetcher does not check robots; the runner does. A disallowed
    # path must raise before any page fetch.
    from civicsignals_api.modules.recipes.runner import RobotsDisallowedError

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text=_HTML)

    recipe = recipes_services.parse_recipe(_RECIPE)
    fetcher = _fetcher_with_handler(handler)
    runner = recipes_services.make_runner(recipe, fetcher, clock=FakeClock())
    pointer = runner.discover(["https://webs.des.wa.gov/private/rfp"])[0]
    with pytest.raises(RobotsDisallowedError):
        runner.fetch(pointer)


def test_runner_applies_politeness_for_static_fetcher() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_HTML)

    recipe = recipes_services.parse_recipe(_RECIPE)  # politeness_seconds=10
    fetcher = _fetcher_with_handler(handler)
    clock = FakeClock()
    runner = recipes_services.make_runner(recipe, fetcher, clock=clock)
    for path in ("a", "b", "c"):
        runner.fetch(runner.discover([f"https://webs.des.wa.gov/{path}"])[0])
    # First fetch no wait; each later same-host fetch waits the full window.
    assert clock.slept == [10.0, 10.0]


def test_crawl_with_connector_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # crawl_recipe_with_connector selects http_static, builds its fetcher, runs the
    # lifecycle. Patch the connector's build_fetcher to inject a mocked transport.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=_HTML)

    def fake_build(self: HttpStaticConnector) -> HttpxFetcher:
        return _fetcher_with_handler(handler)

    monkeypatch.setattr(HttpStaticConnector, "build_fetcher", fake_build)
    records = ingestion_services.crawl_recipe_with_connector(
        "wa-state-webs", ["https://webs.des.wa.gov/rfp-1"], clock=FakeClock()
    )
    assert len(records) == 1
    # The served page has <h1 class="solicitation-title">RFP 1</h1>, which the
    # wa-state-webs recipe's primary title selector matches.
    assert records[0].fields["title"] == "RFP 1"
    assert records[0].recipe_id == "wa-state-webs"
