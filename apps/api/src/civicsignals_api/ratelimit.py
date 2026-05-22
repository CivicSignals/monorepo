"""Per-client rate limiting for the public, unauthenticated API surface (P4).

The public read endpoints — ``GET /signals/{id}/public``, ``GET /signals/{id}/sources``
(doc 13 §4.1, §4.6) and the public entity directory reads (doc 07 §3, C1) — are
*open*: no bearer token, no ``X-Workspace-Id``. That makes them the one surface a
scraper can hit at scale. P4's mandate ("polite to crawlers, hostile to scrapers")
is implemented here: a per-client fixed-window limiter that is generous enough that
a human browsing the directory or a well-behaved crawler honouring ``Crawl-delay``
never trips it, but throttles a tight scrape loop.

Why fixed-window (not a token bucket): it needs exactly two Redis ops on the hot
path — ``INCR`` plus a first-hit ``EXPIRE`` — and no Lua, no read-modify-write
race. The minor burst-at-the-boundary imprecision a fixed window allows is fine
for abuse mitigation; we are not metering paid quota here.

Storage: Redis (already in the stack, doc 06 §8) via the same injectable
:class:`~civicsignals_api.modules.ingestion.locks.RedisClient` Protocol the D4
locks use, so tests pass an in-memory fake and no live Redis is needed in the unit
suite. If Redis is unreachable the limiter **fails open** to a process-local
in-memory counter (a single API replica still gets throttling; we never 500 a
public page because the cache blinked — availability of a public read beats
perfect global accounting, doc 13 §4.6).

Client identity: behind the nginx reverse proxy (``infra/nginx/nginx.conf`` sets
``X-Forwarded-For``/``X-Real-IP``), ``request.client.host`` is the proxy, not the
caller. The left-most ``X-Forwarded-For`` hop is **client-controlled** — nginx
*appends* via ``$proxy_add_x_forwarded_for``, so a scraper can prepend a fake hop
and mint a fresh bucket per request, defeating the limiter. We therefore key on
the value our *trusted* proxy sets: **``X-Real-IP``** (nginx sets it to
``$remote_addr``, the real socket peer) first; then, if absent, the **right-most**
``X-Forwarded-For`` hop (the entry nginx itself appended — the peer it saw);
finally the socket peer (local dev with no proxy). Only applied to public routes;
authenticated routes are never wrapped (they are quota'd elsewhere, e.g. I5
smart-search budget) so this never throttles a logged-in user.

On limit exceeded the dependency raises :class:`~civicsignals_api.problems.ProblemException`
with status ``429`` so the app-level handler renders RFC 7807 ``application/problem+json``
(doc 08 §1.7), carrying ``Retry-After`` and the standard ``X-RateLimit-*`` headers.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request

from civicsignals_api.config import get_settings
from civicsignals_api.problems import ProblemException

log = structlog.get_logger(__name__)

# Redis key namespace for the public-surface limiter (distinct from the D4
# locks' ``civicsignals:ingest:*`` / ``civicsignals:scheduler:*`` namespaces).
_KEY_PREFIX = "civicsignals:ratelimit:public:"


def client_ip(request: Request) -> str:
    """Resolve a *trustworthy* client IP for keying the limiter (P4).

    The left-most ``X-Forwarded-For`` hop is **not** trustworthy: it is
    client-supplied and nginx only *appends* (``$proxy_add_x_forwarded_for``), so a
    scraper that sets ``X-Forwarded-For: <random>`` gets a brand-new bucket on every
    request. We instead trust only what our reverse proxy stamps:

    1. ``X-Real-IP`` — nginx sets this to ``$remote_addr`` (the real socket peer it
       saw). This is the authoritative client identity behind the proxy.
    2. the **right-most** ``X-Forwarded-For`` hop — the entry nginx itself appended
       (again ``$remote_addr``); used only if ``X-Real-IP`` is somehow absent.
    3. the raw socket peer (``request.client.host``) — local dev with no proxy.

    Returns ``"unknown"`` only if even the socket peer is absent (it shouldn't be
    over real transport) — all such callers then share one bucket.

    NOTE: this assumes the app is reached only via the trusted nginx (the self-host
    deployment, doc 06 §10). If ever exposed directly without a proxy stripping
    inbound XFF/X-Real-IP, both header sources become client-spoofable and only the
    socket peer can be trusted; the deployment must front the API with the proxy.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Right-most hop = the peer the trusted proxy actually saw (it appends).
        last = forwarded.rsplit(",", 1)[-1].strip()
        if last:
            return last
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class _InProcessWindow:
    """Process-local fixed-window counter used as the Redis-unavailable fallback.

    Keeps a per-key deque of hit timestamps and prunes those older than the window
    on each call. Bounded by the number of distinct clients seen within a window;
    pruning keeps it from growing without bound. Thread-safe (the limiter may be
    hit from threadpool-offloaded sync code, though our routes are async).
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def incr(self, key: str, *, window_seconds: int, now: float) -> int:
        # TODO (NIT, follow-up): idle keys whose deques fully drain are pruned to
        # empty deques but the dict entry lingers until the key is hit again; a
        # periodic sweep of empty-deque entries would bound the dict by *active*
        # clients rather than all-clients-ever-seen-this-process.
        with self._lock:
            dq = self._hits.get(key)
            if dq is None:
                dq = deque()
                self._hits[key] = dq
            cutoff = now - window_seconds
            while dq and dq[0] <= cutoff:
                dq.popleft()
            dq.append(now)
            return len(dq)


# One process-wide fallback store shared by every limiter instance.
_FALLBACK = _InProcessWindow()


def _redis_incr_window(key: str, *, window_seconds: int) -> int | None:
    """Atomically bump the fixed-window counter in Redis; return the new count.

    Two ops on the hot path: ``INCR`` (creating the key at 1 on the first hit of a
    window) and, only when the count is 1, ``EXPIRE`` to arm the window so the key
    self-evicts when the window closes. Returns ``None`` (signalling "fall back")
    if Redis is unavailable for *any* reason — the public surface stays up.
    """
    try:
        from civicsignals_api.modules.ingestion.locks import get_redis_client

        client = get_redis_client()
        count = client.incr(key)
        if count == 1:
            client.expire(key, window_seconds)
        return int(count)
    except Exception as exc:
        # Fail open — never 500 a public read because the cache blinked.
        log.warning("ratelimit.redis_unavailable", error=str(exc))
        return None


class RateLimiter:
    """A per-client fixed-window limiter (``limit`` requests per ``window_seconds``).

    ``namespace`` separates buckets for differently-tuned route groups (so the
    signal reads and the directory reads can have independent caps without one
    starving the other). The instance is callable as a FastAPI dependency.
    """

    def __init__(self, *, namespace: str, limit: int, window_seconds: int) -> None:
        self._namespace = namespace
        self._limit = limit
        self._window = window_seconds

    @property
    def enabled(self) -> bool:
        # A non-positive limit disables the limiter entirely (escape hatch for
        # self-hosters who front the API with their own WAF / rate limiting).
        return self._limit > 0

    def _count(self, ip: str) -> int:
        key = f"{_KEY_PREFIX}{self._namespace}:{ip}"
        count = _redis_incr_window(key, window_seconds=self._window)
        if count is None:
            count = _FALLBACK.incr(key, window_seconds=self._window, now=time.time())
        return count

    async def __call__(self, request: Request) -> None:
        """FastAPI dependency: count this hit and 429 if the window is exhausted."""
        if not self.enabled:
            return
        ip = client_ip(request)
        count = self._count(ip)
        remaining = max(0, self._limit - count)
        headers = {
            "X-RateLimit-Limit": str(self._limit),
            "X-RateLimit-Remaining": str(remaining),
            "X-RateLimit-Window": str(self._window),
        }
        if count > self._limit:
            log.info("ratelimit.exceeded", namespace=self._namespace, ip=ip, count=count)
            raise ProblemException(
                status=429,
                code="rate_limited",
                title="Rate limited",
                detail=(
                    "Too many requests to the public API from your client. "
                    f"Limit is {self._limit} requests per {self._window}s; "
                    "retry after the window resets."
                ),
                # TODO (NIT, follow-up): Retry-After is the full window length, not
                # the time remaining until *this* fixed window resets, so it can
                # over-state the wait near the window boundary. Tightening it would
                # need the key's TTL (Redis ``TTL``) on the hot path.
                headers={**headers, "Retry-After": str(self._window)},
            )
        # Stash the headers so a success path could echo them too (the routes do
        # not currently surface them on 200s — kept minimal — but the dependency
        # makes them available on ``request.state`` for any future middleware).
        request.state.ratelimit_headers = headers


def _limiter_for(namespace: str) -> RateLimiter:
    """Build a limiter for ``namespace`` from current settings (read per request).

    Reading settings on each build keeps the limit/window configurable at runtime
    and lets tests monkeypatch ``get_settings`` (or the cache) without re-importing
    the routes. ``RateLimiter`` itself holds no per-request state, so re-building is
    cheap — the only shared state (Redis / the in-process fallback) lives outside.
    """
    settings = get_settings()
    return RateLimiter(
        namespace=namespace,
        limit=settings.public_rate_limit_per_window,
        window_seconds=settings.public_rate_limit_window_seconds,
    )


async def public_signal_limiter(request: Request) -> None:
    """FastAPI dependency: rate-limit the public signal reads (``/public`` + ``/sources``)."""
    await _limiter_for("signals")(request)


async def public_directory_limiter(request: Request) -> None:
    """FastAPI dependency: rate-limit the public entity directory reads (list / get / children)."""
    await _limiter_for("directory")(request)


# Type alias for the dependency callables (kept explicit for mypy --strict).
RateLimitDependency = Callable[[Request], Awaitable[None]]


__all__ = [
    "RateLimitDependency",
    "RateLimiter",
    "client_ip",
    "public_directory_limiter",
    "public_signal_limiter",
]
