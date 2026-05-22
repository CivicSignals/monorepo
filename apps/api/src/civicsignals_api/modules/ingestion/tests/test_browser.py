"""Tests for the headless-browser fetcher + pool (D2; doc 18 §1 cat. D, §4).

No real Chromium runs here: a fake browser/launcher exercises the pool's
bounded-concurrency + recycle lifecycle and the fetcher's conformance to the
runner's ``Fetcher`` Protocol. The lazy-import guard is checked by simulating
Playwright being absent. An optional real-Chromium smoke test is gated behind
``CIVIC_BROWSER_SMOKE=1`` and skipped by default.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from civicsignals_api.modules.ingestion.browser import (
    BrowserFetcher,
    BrowserPool,
    BrowserUnavailableError,
)
from civicsignals_api.modules.recipes.runner import Fetcher

# ---------------------------------------------------------------------------
# Fakes: an in-memory Playwright stand-in (Browser -> Context -> Page).
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str]) -> None:
        self.status = status
        self._headers = headers

    async def all_headers(self) -> dict[str, str]:
        return self._headers


class FakePage:
    def __init__(self, html: str, status: int, headers: dict[str, str]) -> None:
        self._html = html
        self._status = status
        self._headers = headers
        self.goto_calls: list[str] = []
        self.extra_headers: dict[str, str] = {}
        self.waited_selectors: list[str] = []

    async def set_extra_http_headers(self, headers: dict[str, str]) -> None:
        self.extra_headers.update(headers)

    # `timeout` mirrors Playwright's real Page.* signatures (the fetcher passes
    # it by keyword), so ASYNC109 is intentional here.
    async def goto(self, url: str, *, wait_until: str, timeout: int) -> FakeResponse:  # noqa: ASYNC109
        self.goto_calls.append(url)
        return FakeResponse(self._status, self._headers)

    async def wait_for_selector(self, selector: str, *, timeout: int) -> None:  # noqa: ASYNC109
        self.waited_selectors.append(selector)

    async def content(self) -> str:
        return self._html


class FakeContext:
    def __init__(self, page: FakePage) -> None:
        self._page = page
        self.closed = False
        # How many leases are concurrently live through this context's browser.
        self.concurrent_at_open = 0

    async def new_page(self) -> FakePage:
        return self._page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(self, html: str, status: int, headers: dict[str, str]) -> None:
        self._html = html
        self._status = status
        self._headers = headers
        self.closed = False
        self.contexts: list[FakeContext] = []
        self.live = 0
        self.max_live = 0

    async def new_context(self) -> FakeContext:
        self.live += 1
        self.max_live = max(self.max_live, self.live)
        page = FakePage(self._html, self._status, self._headers)
        ctx = FakeContext(page)
        ctx.concurrent_at_open = self.live
        self.contexts.append(ctx)
        return ctx

    async def close(self) -> None:
        self.closed = True


class FakeLauncher:
    """A :class:`BrowserLauncher` that hands out a single :class:`FakeBrowser`."""

    def __init__(
        self,
        *,
        html: str = "<html><body><h1>ok</h1></body></html>",
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.html = html
        self.status = status
        self.headers = headers or {"content-type": "text/html"}
        self.launches = 0
        self.shutdowns = 0
        self.browser: FakeBrowser | None = None

    async def launch(self) -> FakeBrowser:
        self.launches += 1
        self.browser = FakeBrowser(self.html, self.status, self.headers)
        return self.browser

    async def shutdown(self) -> None:
        self.shutdowns += 1


# A lease that decrements its browser's live counter on release, so the pool's
# release path is reflected in `max_live` bookkeeping.
async def _lease_and_count(pool: BrowserPool, browser_holder: FakeLauncher) -> None:
    async with pool.page():
        await asyncio.sleep(0.02)
    assert browser_holder.browser is not None
    browser_holder.browser.live -= 1


# ---------------------------------------------------------------------------
# Pool: lifecycle, bounded concurrency, recycling
# ---------------------------------------------------------------------------


async def test_pool_launches_lazily_on_first_use() -> None:
    launcher = FakeLauncher()
    pool = BrowserPool(launcher, size=2)
    assert launcher.launches == 0  # nothing launched at construction
    async with pool.page() as page:
        assert isinstance(page, FakePage)
    assert launcher.launches == 1
    await pool.aclose()


async def test_pool_launches_browser_once_across_leases() -> None:
    launcher = FakeLauncher()
    pool = BrowserPool(launcher, size=3)
    async with pool.page():
        pass
    async with pool.page():
        pass
    assert launcher.launches == 1  # browser reused, not relaunched
    await pool.aclose()


async def test_pool_recycles_contexts_per_lease() -> None:
    launcher = FakeLauncher()
    pool = BrowserPool(launcher, size=2)
    for _ in range(3):
        async with pool.page():
            pass
    # A fresh context per lease, every one closed on release.
    assert pool.contexts_opened == 3
    assert pool.contexts_closed == 3
    assert launcher.browser is not None
    assert all(ctx.closed for ctx in launcher.browser.contexts)
    await pool.aclose()


async def test_pool_bounds_concurrency() -> None:
    launcher = FakeLauncher()
    pool = BrowserPool(launcher, size=2)
    # Fire 6 leases at once; the semaphore must cap simultaneous contexts at 2.
    await asyncio.gather(*(_lease_and_count(pool, launcher) for _ in range(6)))
    assert launcher.browser is not None
    assert launcher.browser.max_live == 2  # never exceeded the pool size
    assert pool.contexts_opened == 6
    assert pool.contexts_closed == 6
    await pool.aclose()


async def test_pool_releases_slot_when_new_context_fails() -> None:
    class FlakyBrowser(FakeBrowser):
        async def new_context(self) -> FakeContext:
            raise RuntimeError("boom")

    class FlakyLauncher(FakeLauncher):
        async def launch(self) -> FakeBrowser:
            self.launches += 1
            self.browser = FlakyBrowser(self.html, self.status, self.headers)
            return self.browser

    launcher = FlakyLauncher()
    pool = BrowserPool(launcher, size=1)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="boom"):
            async with pool.page():
                pass
    # If the failed acquire leaked the semaphore slot, the 2nd attempt would
    # deadlock instead of raising — reaching here proves the slot was released.
    await pool.aclose()


async def test_pool_closes_context_when_new_page_fails() -> None:
    class BadPageContext(FakeContext):
        async def new_page(self) -> FakePage:
            raise RuntimeError("page boom")

    class BadPageBrowser(FakeBrowser):
        async def new_context(self) -> FakeContext:
            self.live += 1
            self.max_live = max(self.max_live, self.live)
            ctx = BadPageContext(FakePage(self._html, self._status, self._headers))
            self.contexts.append(ctx)
            return ctx

    class BadPageLauncher(FakeLauncher):
        async def launch(self) -> FakeBrowser:
            self.launches += 1
            self.browser = BadPageBrowser(self.html, self.status, self.headers)
            return self.browser

    launcher = BadPageLauncher()
    pool = BrowserPool(launcher, size=1)
    with pytest.raises(RuntimeError, match="page boom"):
        await pool.acquire()
    # The context created before new_page() failed must be closed (no leak), and
    # the slot freed so a follow-up lease doesn't deadlock.
    assert launcher.browser is not None
    assert launcher.browser.contexts[0].closed
    assert pool.contexts_opened == pool.contexts_closed == 1
    with pytest.raises(RuntimeError, match="page boom"):
        await pool.acquire()  # would hang if the slot leaked
    await pool.aclose()


async def test_pool_aclose_is_idempotent_and_closes_browser() -> None:
    launcher = FakeLauncher()
    pool = BrowserPool(launcher, size=1)
    async with pool.page():
        pass
    await pool.aclose()
    await pool.aclose()  # idempotent
    assert launcher.browser is not None
    assert launcher.browser.closed
    assert launcher.shutdowns == 1


async def test_pool_rejects_size_below_one() -> None:
    with pytest.raises(ValueError, match="size must be >= 1"):
        BrowserPool(FakeLauncher(), size=0)


async def test_pool_acquire_after_close_raises() -> None:
    pool = BrowserPool(FakeLauncher(), size=1)
    await pool.aclose()
    with pytest.raises(RuntimeError, match="closed"):
        await pool.acquire()


# ---------------------------------------------------------------------------
# Fetcher: Fetcher-Protocol conformance + render behavior
# ---------------------------------------------------------------------------


def test_fetcher_conforms_to_runner_fetcher_protocol() -> None:
    pool = BrowserPool(FakeLauncher(), size=1)
    fetcher = BrowserFetcher(pool)
    # runtime_checkable Protocol: shape must match the static fetcher's.
    assert isinstance(fetcher, Fetcher)
    fetcher.close()


def test_fetcher_renders_and_returns_status_html_headers() -> None:
    launcher = FakeLauncher(
        html="<html><body><main>rendered</main></body></html>",
        status=201,
        headers={"x-test": "1"},
    )
    pool = BrowserPool(launcher, size=2)
    with BrowserFetcher(pool) as fetcher:
        status, html, headers = fetcher.fetch(
            "https://spa.example/listing", user_agent="UA/1", max_redirects=5
        )
    assert status == 201
    assert "rendered" in html
    assert headers["x-test"] == "1"
    # The user agent was forwarded to the page.
    assert launcher.browser is not None
    assert launcher.browser.contexts[0]._page.extra_headers["User-Agent"] == "UA/1"


def test_fetcher_waits_for_ready_selector_when_configured() -> None:
    launcher = FakeLauncher()
    pool = BrowserPool(launcher, size=1)
    with BrowserFetcher(pool, ready_selector=".results .row") as fetcher:
        fetcher.fetch("https://spa.example/x", user_agent="UA", max_redirects=5)
    assert launcher.browser is not None
    assert launcher.browser.contexts[0]._page.waited_selectors == [".results .row"]


def test_fetcher_reuses_pool_across_fetches() -> None:
    launcher = FakeLauncher()
    pool = BrowserPool(launcher, size=2)
    with BrowserFetcher(pool) as fetcher:
        fetcher.fetch("https://spa.example/a", user_agent="UA", max_redirects=5)
        fetcher.fetch("https://spa.example/b", user_agent="UA", max_redirects=5)
    assert launcher.launches == 1  # one browser drove both fetches
    assert pool.contexts_opened == 2  # but a fresh context each time
    fetcher.close()


def test_fetcher_close_tears_down_owned_pool() -> None:
    launcher = FakeLauncher()
    fetcher = BrowserFetcher()  # constructs its own pool
    # Swap in our fake pool so close() exercises the owned-pool teardown path.
    fetcher._pool = BrowserPool(launcher, size=1)
    fetcher._owns_pool = True
    fetcher.fetch("https://spa.example/x", user_agent="UA", max_redirects=5)
    fetcher.close()
    assert launcher.browser is not None
    assert launcher.browser.closed  # owned pool was closed


def test_fetcher_close_leaves_shared_pool_open() -> None:
    launcher = FakeLauncher()
    pool = BrowserPool(launcher, size=1)
    fetcher = BrowserFetcher(pool)  # shared pool, not owned
    fetcher.fetch("https://spa.example/x", user_agent="UA", max_redirects=5)
    fetcher.close()
    assert launcher.browser is not None
    assert not launcher.browser.closed  # caller still owns the pool


def test_fetcher_robots_txt_uses_plain_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    captured: dict[str, object] = {}

    class _Resp:
        status_code = 200
        text = "User-agent: *\nDisallow: /private\n"

    def fake_get(url: str, **kwargs: object) -> _Resp:
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return _Resp()

    monkeypatch.setattr(httpx, "get", fake_get)
    fetcher = BrowserFetcher(BrowserPool(FakeLauncher(), size=1))
    body = fetcher.robots_txt("https://spa.example/some/page?q=1", user_agent="UA")
    assert body is not None
    assert "Disallow: /private" in body
    assert captured["url"] == "https://spa.example/robots.txt"
    fetcher.close()


def test_fetcher_robots_txt_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    def boom(url: str, **kwargs: object) -> object:
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(httpx, "get", boom)
    fetcher = BrowserFetcher(BrowserPool(FakeLauncher(), size=1))
    assert fetcher.robots_txt("https://spa.example/x", user_agent="UA") is None
    fetcher.close()


def test_fetcher_robots_txt_none_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class _Resp:
        status_code = 404
        text = "not found"

    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: _Resp())
    fetcher = BrowserFetcher(BrowserPool(FakeLauncher(), size=1))
    assert fetcher.robots_txt("https://spa.example/x", user_agent="UA") is None
    fetcher.close()


# ---------------------------------------------------------------------------
# Lazy-import guard: a clear error when the ingestion extra is absent
# ---------------------------------------------------------------------------


async def test_lazy_import_guard_raises_when_playwright_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real_import = builtins.__import__

    def deny_playwright(name: str, *args: object, **kwargs: object) -> object:
        if name == "playwright" or name.startswith("playwright."):
            raise ImportError("No module named 'playwright'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", deny_playwright)

    # The default launcher must surface BrowserUnavailableError on launch.
    pool = BrowserPool(size=1)  # default _PlaywrightLauncher
    with pytest.raises(BrowserUnavailableError) as exc:
        await pool.acquire()
    msg = str(exc.value)
    assert "ingestion" in msg
    assert "playwright install chromium" in msg


def test_browser_unavailable_error_message_is_actionable() -> None:
    err = BrowserUnavailableError()
    text = str(err)
    assert "ingestion" in text
    assert "playwright" in text.lower()


# ---------------------------------------------------------------------------
# Optional real-Chromium smoke test (skipped by default; opt in with the flag)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("CIVIC_BROWSER_SMOKE") != "1",
    reason="real-Chromium smoke test; set CIVIC_BROWSER_SMOKE=1 and "
    "`playwright install chromium` to run",
)
def test_real_chromium_renders_data_url() -> None:  # pragma: no cover - opt-in
    html = "<html><body><h1 id='hdr'>Hello SPA</h1></body></html>"
    with BrowserFetcher(ready_selector="#hdr") as fetcher:
        _status, rendered, _headers = fetcher.fetch(
            f"data:text/html,{html}", user_agent="CivicSignalsBot/test", max_redirects=5
        )
    assert "Hello SPA" in rendered
