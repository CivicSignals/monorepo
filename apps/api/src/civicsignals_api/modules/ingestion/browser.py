"""Playwright-backed headless-browser fetcher + pool (D2; doc 18 §1 cat. D, §4).

This is the ``http_browser`` fetch path (doc 18 §5 wave 4): an alternative
:class:`~civicsignals_api.modules.recipes.runner.Fetcher` for JS-rendered pages
the static HTTP fetcher (D6) can't read, because their content only exists after
JavaScript executes. JS-heavy recipes select it by declaring
``connector: http_browser``; the ingestion worker (``tasks.browser_fetch``)
injects a :class:`BrowserFetcher` into the recipe runner in place of the static
one. Everything else in the lifecycle is unchanged — the runner still owns
robots.txt + politeness, version pinning, and the extract fallback chain.

Why a *pool*: a single Playwright page can balloon to hundreds of MB mid-render
(doc 18 §6.1), so we bound how many render concurrently *per worker process*
with :class:`BrowserPool`. Burst beyond the pool is absorbed by ingest queue
depth and horizontal autoscaling (O3's Helm chart scales ``worker_ingest`` on
queue depth — ``workerIngest.hpa`` in ``infra/helm/.../templates/hpa.yaml``),
**not** by an unbounded in-process pool that would OOM the worker.

Playwright lives in the ``ingestion`` optional extra (pyproject), so it is
**imported lazily** — the lean ``api`` image (and any process that merely
imports this module) does not need it. Using the fetcher without the extra
installed raises :class:`BrowserUnavailableError` with a clear remedy.

The fetcher is **policy-light on purpose**: it does *not* check robots.txt or
apply the politeness window itself. Those live in
:class:`~civicsignals_api.modules.recipes.runner.RecipeRunner`, which calls every
``Fetcher`` (static or browser) through the same path — so the browser path can
never accidentally bypass the legal/ethical posture (doc 18 §2.2). The runner's
``robots_txt`` hook here is a cheap plain HTTP GET (no browser needed).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Literal, Protocol

from civicsignals_api.config import get_settings

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Browser, BrowserContext, Page, Playwright


WaitUntil = Literal["load", "domcontentloaded", "networkidle"]


# ----------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------


class BrowserUnavailableError(RuntimeError):
    """Playwright (the ``ingestion`` extra) is not installed.

    Raised the moment the browser fetch path is exercised without its optional
    dependency, so the failure is a clear, actionable message rather than an
    obscure ``ModuleNotFoundError`` deep in a worker.
    """

    def __init__(self, cause: ImportError | None = None) -> None:
        super().__init__(
            "the headless-browser fetcher requires Playwright, which ships in the "
            "'ingestion' optional extra. Install it and the browser binary: "
            "`uv sync --extra ingestion && uv run playwright install chromium`. "
            "Only worker_ingest carries this; the lean api image does not."
        )
        self.__cause__ = cause


def _import_playwright() -> object:
    """Lazily import :mod:`playwright.async_api`, or raise a clear guard error."""
    try:
        from playwright import async_api
    except ImportError as exc:  # pragma: no cover - exercised via the guard test
        raise BrowserUnavailableError(exc) from exc
    return async_api


# ----------------------------------------------------------------------------
# Browser launcher seam (so the pool is testable without a real browser)
# ----------------------------------------------------------------------------


class BrowserLauncher(Protocol):
    """Launch a browser. The default drives Playwright; tests inject a fake.

    Returns an object exposing the slice of Playwright's ``Browser`` API the pool
    uses (``new_context`` / ``close``). Keeping this a seam means the pool's
    bounded-concurrency + recycle logic is unit-tested with an in-memory fake and
    no Chromium download.
    """

    async def launch(self) -> Browser:
        """Start the browser process and return the handle."""
        ...

    async def shutdown(self) -> None:
        """Tear down any launcher-owned resources (e.g. the Playwright driver)."""
        ...


class _PlaywrightLauncher:
    """Default :class:`BrowserLauncher`: a headless Chromium via Playwright.

    Owns the ``Playwright`` driver lifetime alongside the ``Browser`` so that
    :meth:`shutdown` stops the driver subprocess cleanly (otherwise it lingers).
    """

    def __init__(self, *, headless: bool, launch_timeout_ms: int) -> None:
        self._headless = headless
        self._launch_timeout_ms = launch_timeout_ms
        self._playwright: Playwright | None = None

    async def launch(self) -> Browser:
        async_api = _import_playwright()
        # ``start()`` boots the Playwright driver subprocess; we keep the handle
        # so shutdown can stop it. mypy can't see the lazy import's types.
        self._playwright = await async_api.async_playwright().start()  # type: ignore[attr-defined]
        assert self._playwright is not None
        try:
            return await self._playwright.chromium.launch(
                headless=self._headless,
                timeout=self._launch_timeout_ms,
            )
        except BaseException:
            # Chromium failed to launch (e.g. binary missing / timeout): stop the
            # driver subprocess we just started so it doesn't leak, then re-raise.
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None


# ----------------------------------------------------------------------------
# Async browser pool: bounded concurrency + lifecycle
# ----------------------------------------------------------------------------


class BrowserPool:
    """Manage one shared browser with a bounded number of concurrent pages.

    Lifecycle (doc 18 §6.1 "resource isolation"):

    * **lazy launch** — the browser starts on the first :meth:`page` use, not at
      import, so processes that never render a page pay nothing.
    * **bounded concurrency** — at most ``size`` contexts/pages render at once,
      gated by a semaphore. A fresh, isolated :class:`BrowserContext` per lease
      (cookies/storage don't bleed between fetches) is created on acquire and
      closed on release — *recycling* per use keeps memory flat across a long
      run and side-steps the slow leak of reused contexts.
    * **clean shutdown** — :meth:`aclose` closes the browser and stops the driver.

    The pool is async and not thread-safe by design: each worker process owns one
    pool, driven from a single event loop (:class:`BrowserFetcher` bridges the
    runner's synchronous ``Fetcher`` interface to it).
    """

    def __init__(
        self,
        launcher: BrowserLauncher | None = None,
        *,
        size: int | None = None,
        headless: bool | None = None,
        launch_timeout_ms: int | None = None,
    ) -> None:
        settings = get_settings()
        resolved_size = settings.browser_pool_size if size is None else size
        if resolved_size < 1:
            raise ValueError("browser pool size must be >= 1")
        self._size = resolved_size
        self._launcher = launcher or _PlaywrightLauncher(
            headless=settings.browser_headless if headless is None else headless,
            launch_timeout_ms=(
                settings.browser_launch_timeout_ms
                if launch_timeout_ms is None
                else launch_timeout_ms
            ),
        )
        self._semaphore = asyncio.Semaphore(resolved_size)
        # Serializes the (idempotent) first-use launch so concurrent leases don't
        # race to start two browsers.
        self._launch_lock = asyncio.Lock()
        self._browser: Browser | None = None
        self._closed = False
        # Count of contexts created/closed over the pool's life — recycling means
        # these stay equal once all leases are released (asserted in tests).
        self.contexts_opened = 0
        self.contexts_closed = 0

    @property
    def size(self) -> int:
        return self._size

    async def _ensure_browser(self) -> Browser:
        if self._closed:
            raise RuntimeError("browser pool is closed")
        if self._browser is None:
            async with self._launch_lock:
                if self._browser is None:  # re-check under the lock
                    self._browser = await self._launcher.launch()
        return self._browser

    async def acquire(self) -> tuple[BrowserContext, Page]:
        """Lease a fresh context + page, blocking until the pool has a free slot.

        Callers MUST pair this with :meth:`release`; prefer the :meth:`page`
        context manager, which guarantees release even on error.
        """
        await self._semaphore.acquire()
        context: BrowserContext | None = None
        try:
            browser = await self._ensure_browser()
            context = await browser.new_context()
            self.contexts_opened += 1
            page = await context.new_page()
        except BaseException:
            # If new_page() failed after the context opened, best-effort close it
            # so we don't leak a context (and keep the opened/closed tally even).
            if context is not None:
                try:
                    await context.close()
                    self.contexts_closed += 1
                except Exception:
                    pass  # cleanup is best-effort; the original error re-raises
            # Never leak the slot if context/page creation failed.
            self._semaphore.release()
            raise
        return context, page

    async def release(self, context: BrowserContext) -> None:
        """Close a leased context (recycle) and free its concurrency slot."""
        try:
            await context.close()
            self.contexts_closed += 1
        finally:
            self._semaphore.release()

    def page(self) -> _PageLease:
        """``async with pool.page() as page:`` — acquire, then always release."""
        return _PageLease(self)

    async def aclose(self) -> None:
        """Close the browser and stop the launcher. Idempotent."""
        if self._closed:
            return
        self._closed = True
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        await self._launcher.shutdown()

    async def __aenter__(self) -> BrowserPool:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


class _PageLease:
    """Async-context-manager lease of one pool page; releases on exit."""

    def __init__(self, pool: BrowserPool) -> None:
        self._pool = pool
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> Page:
        self._context, page = await self._pool.acquire()
        return page

    async def __aexit__(self, *exc: object) -> None:
        if self._context is not None:
            await self._pool.release(self._context)
            self._context = None


# ----------------------------------------------------------------------------
# The fetcher: implements the runner's synchronous Fetcher Protocol
# ----------------------------------------------------------------------------


class BrowserFetcher:
    """Headless-browser :class:`Fetcher` for the recipe runner (D2).

    Conforms to the runner's ``Fetcher`` Protocol shape — synchronous
    ``fetch(url, *, user_agent, max_redirects) -> (status, html, headers)`` and
    ``robots_txt`` — so it drops in wherever the static fetcher does. Internally
    it drives the async :class:`BrowserPool`; the synchronous boundary is bridged
    by running the render coroutine on a private event loop (the runner, called
    from a Celery worker, is itself synchronous — mirrors how
    ``GatewayFieldExtractor`` bridges to the async gateway).

    It deliberately does **not** enforce robots/politeness — the runner does, for
    every fetcher uniformly (doc 18 §2.2). ``robots_txt`` is a plain HTTP GET (no
    browser needed for a tiny text file).
    """

    def __init__(
        self,
        pool: BrowserPool | None = None,
        *,
        nav_timeout_ms: int | None = None,
        wait_until: WaitUntil | None = None,
        ready_selector: str | None = None,
        selector_timeout_ms: int | None = None,
    ) -> None:
        settings = get_settings()
        self._pool = pool if pool is not None else BrowserPool()
        self._owns_pool = pool is None
        self._nav_timeout_ms = (
            settings.browser_nav_timeout_ms if nav_timeout_ms is None else nav_timeout_ms
        )
        self._wait_until: WaitUntil = (
            settings.browser_wait_until if wait_until is None else wait_until
        )
        # Optional recipe-supplied selector to await before reading content — the
        # SPA equivalent of "wait for the listing to render" (doc 18 §2.1 cat. D).
        self._ready_selector = ready_selector
        self._selector_timeout_ms = (
            settings.browser_selector_timeout_ms
            if selector_timeout_ms is None
            else selector_timeout_ms
        )
        # Lazily-created private loop + thread used to drive the async pool from
        # the synchronous Fetcher interface (see _run).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()

    # -- sync<->async bridge ------------------------------------------------
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Return a long-lived background event loop dedicated to this fetcher.

        A persistent loop (vs. ``asyncio.run`` per call) lets the pool's browser
        and its semaphore survive across fetches, which is the whole point of
        pooling. The loop runs on a daemon thread so a forgotten ``close`` never
        blocks process exit.
        """
        with self._loop_lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=loop.run_forever,
                    name="browser-fetcher-loop",
                    daemon=True,
                )
                thread.start()
                self._loop = loop
                self._loop_thread = thread
            return self._loop

    def _run[T](self, coro: Coroutine[object, object, T]) -> T:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    # -- Fetcher Protocol ---------------------------------------------------
    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        """Render ``url`` and return ``(status_code, html, headers)``.

        ``max_redirects`` is accepted for interface parity; Playwright follows
        redirects internally and does not expose a per-navigation cap, so it is
        advisory here (the runner still records it for provenance).
        """
        return self._run(self._fetch_async(url, user_agent=user_agent))

    async def _fetch_async(self, url: str, *, user_agent: str) -> tuple[int, str, dict[str, str]]:
        async with self._pool.page() as page:
            await page.set_extra_http_headers({"User-Agent": user_agent})
            response = await page.goto(
                url,
                wait_until=self._wait_until,
                timeout=self._nav_timeout_ms,
            )
            if self._ready_selector is not None:
                await page.wait_for_selector(
                    self._ready_selector, timeout=self._selector_timeout_ms
                )
            html = await page.content()
            if response is None:
                # ``about:blank``-style navigation with no network response.
                return 200, html, {}
            status = response.status
            headers = dict(await response.all_headers())
        return status, html, headers

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        """Fetch ``<scheme>://<host>/robots.txt`` via plain HTTP (no browser).

        robots.txt is a small static text file; rendering it in a browser would
        be wasteful. Returns ``None`` on any miss (absent file / network error),
        which the runner treats as permissive (doc 18 §2.2).
        """
        from urllib.parse import urljoin, urlparse

        import httpx

        robots_url = urljoin(f"{urlparse(url).scheme}://{urlparse(url).netloc}", "/robots.txt")
        try:
            response = httpx.get(
                robots_url,
                headers={"User-Agent": user_agent},
                timeout=10.0,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return response.text

    def close(self) -> None:
        """Close the owned pool (if any) and stop the background loop.

        Safe to call once per fetcher; a fetcher given a shared pool leaves that
        pool's lifecycle to its owner.
        """
        if self._loop is not None:
            if self._owns_pool:
                self._run(self._pool.aclose())
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread is not None:
                self._loop_thread.join(timeout=5.0)
            # Only close a loop whose thread has actually stopped: closing a still-
            # running loop raises RuntimeError. If the thread is wedged we drop the
            # references and let the daemon thread die with the process rather than
            # crash shutdown.
            if self._loop_thread is None or not self._loop_thread.is_alive():
                self._loop.close()
            self._loop = None
            self._loop_thread = None

    def __enter__(self) -> BrowserFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = [
    "BrowserFetcher",
    "BrowserLauncher",
    "BrowserPool",
    "BrowserUnavailableError",
    "WaitUntil",
]
