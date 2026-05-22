# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the public-surface per-client rate limiter (P4).

The limiter (``civicsignals_api.ratelimit``) throttles only the *public,
unauthenticated* read endpoints — the public signal projection / source citations
and the entity directory — keyed per client IP, fixed-window. These tests cover:

- the window allows up to the cap, then 429s, with ``Retry-After`` + ``X-RateLimit-*``
  headers (RFC 7807 ``application/problem+json``);
- it keys per client (two IPs have independent budgets);
- ``X-Forwarded-For`` (the nginx proxy header) is honoured — the left-most hop is
  the client, not the proxy socket peer;
- a non-positive limit disables it entirely;
- Redis-unavailable falls back to the in-process counter (still throttles a single
  replica) rather than failing the public read;
- the limiter dependency is wired onto the public routes but NOT onto the
  authenticated ``/feed`` / admin fuzzy-review routes.

No DB or live Redis required: the limiter takes an injectable Redis client (the
same Protocol the D4 locks use) and a fake ``Request`` carries the headers.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

from civicsignals_api import ratelimit
from civicsignals_api.problems import ProblemException
from civicsignals_api.ratelimit import RateLimiter, client_ip


def _request(
    *, headers: dict[str, str] | None = None, client_host: str | None = "10.0.0.9"
) -> Request:
    """Build a minimal ASGI ``Request`` carrying the given headers + client peer."""
    raw_headers = Headers(headers or {}).raw
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/signals/x/public",
        "headers": raw_headers,
        "client": (client_host, 12345) if client_host else None,
        "state": {},
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# client_ip — proxy header resolution (P4: honour nginx X-Forwarded-For)
# ---------------------------------------------------------------------------


def test_client_ip_prefers_leftmost_forwarded_for() -> None:
    """X-Forwarded-For's left-most hop is the original client, not the proxy."""
    req = _request(headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1, 10.0.0.2"})
    assert client_ip(req) == "203.0.113.7"


def test_client_ip_falls_back_to_x_real_ip() -> None:
    req = _request(headers={"x-real-ip": "198.51.100.4"})
    assert client_ip(req) == "198.51.100.4"


def test_client_ip_falls_back_to_socket_peer() -> None:
    req = _request(client_host="192.0.2.55")
    assert client_ip(req) == "192.0.2.55"


def test_client_ip_unknown_when_no_peer() -> None:
    req = _request(client_host=None)
    assert client_ip(req) == "unknown"


# ---------------------------------------------------------------------------
# RateLimiter — fixed-window allow / 429 + headers (in-process fallback path)
# ---------------------------------------------------------------------------


@pytest.fixture
def in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the limiter use a fresh in-process counter (no live Redis).

    ``_redis_incr_window`` returns None -> the limiter falls back to the process
    store. We swap in a fresh fallback store so windows don't bleed across tests
    sharing the module-level singleton. (Not autouse: the ``_redis_incr_window``
    tests below exercise the real function and must not have it stubbed.)
    """
    monkeypatch.setattr(ratelimit, "_redis_incr_window", lambda key, *, window_seconds: None)
    monkeypatch.setattr(ratelimit, "_FALLBACK", ratelimit._InProcessWindow())


@pytest.mark.usefixtures("in_process")
async def test_allows_up_to_cap_then_429s() -> None:
    limiter = RateLimiter(namespace="t", limit=3, window_seconds=60)
    req = _request(headers={"x-forwarded-for": "203.0.113.7"})
    # First three requests are within the cap (no exception raised = allowed).
    for _ in range(3):
        await limiter(req)
    # The fourth exceeds the cap.
    with pytest.raises(ProblemException) as exc_info:
        await limiter(req)
    exc = exc_info.value
    assert exc.status == 429
    assert exc.code == "rate_limited"
    assert exc.headers is not None
    assert exc.headers["Retry-After"] == "60"
    assert exc.headers["X-RateLimit-Limit"] == "3"
    assert exc.headers["X-RateLimit-Remaining"] == "0"
    assert exc.headers["X-RateLimit-Window"] == "60"


@pytest.mark.usefixtures("in_process")
async def test_keys_per_client_ip() -> None:
    limiter = RateLimiter(namespace="t", limit=2, window_seconds=60)
    a = _request(headers={"x-forwarded-for": "203.0.113.1"})
    b = _request(headers={"x-forwarded-for": "203.0.113.2"})
    # Exhaust client A.
    await limiter(a)
    await limiter(a)
    with pytest.raises(ProblemException):
        await limiter(a)
    # Client B has its own untouched budget (neither call raises).
    await limiter(b)
    await limiter(b)


@pytest.mark.usefixtures("in_process")
async def test_remaining_header_counts_down() -> None:
    limiter = RateLimiter(namespace="t", limit=5, window_seconds=30)
    req = _request(headers={"x-forwarded-for": "203.0.113.9"})
    await limiter(req)  # count=1
    await limiter(req)  # count=2
    # State headers stashed on the request reflect the remaining budget.
    headers = req.state.ratelimit_headers
    assert headers["X-RateLimit-Remaining"] == "3"


async def test_zero_limit_disables_limiter() -> None:
    limiter = RateLimiter(namespace="t", limit=0, window_seconds=60)
    assert not limiter.enabled
    req = _request(headers={"x-forwarded-for": "203.0.113.7"})
    # Never raises regardless of how many calls.
    for _ in range(50):
        await limiter(req)


async def test_redis_unavailable_falls_back_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis error must NOT propagate — the public read stays up, throttled locally."""

    def _boom(key: str, *, window_seconds: int) -> int | None:
        # Simulate the real fail-open: _redis_incr_window swallows + returns None.
        return None

    monkeypatch.setattr(ratelimit, "_redis_incr_window", _boom)
    monkeypatch.setattr(ratelimit, "_FALLBACK", ratelimit._InProcessWindow())
    limiter = RateLimiter(namespace="t", limit=1, window_seconds=60)
    req = _request(headers={"x-forwarded-for": "203.0.113.7"})
    await limiter(req)
    with pytest.raises(ProblemException) as exc_info:
        await limiter(req)
    assert exc_info.value.status == 429


def test_redis_incr_window_returns_none_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """_redis_incr_window fails open (returns None) when the Redis client raises."""

    class _BoomClient:
        def incr(self, name: str, amount: int = 1) -> int:
            raise RuntimeError("redis down")

        def expire(self, name: str, time: int) -> bool:  # pragma: no cover - never reached
            return True

    monkeypatch.setattr(
        "civicsignals_api.modules.ingestion.locks.get_redis_client",
        lambda: _BoomClient(),
    )
    assert ratelimit._redis_incr_window("k", window_seconds=60) is None


def test_redis_incr_window_arms_expire_on_first_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """First hit of a window INCRs to 1 and arms EXPIRE; subsequent hits only INCR."""
    calls: list[tuple[str, object]] = []

    class _Counter:
        def __init__(self) -> None:
            self._n = 0

        def incr(self, name: str, amount: int = 1) -> int:
            self._n += 1
            calls.append(("incr", name))
            return self._n

        def expire(self, name: str, time: int) -> bool:
            calls.append(("expire", time))
            return True

    counter = _Counter()
    monkeypatch.setattr(
        "civicsignals_api.modules.ingestion.locks.get_redis_client",
        lambda: counter,
    )
    assert ratelimit._redis_incr_window("k", window_seconds=45) == 1
    assert ratelimit._redis_incr_window("k", window_seconds=45) == 2
    # EXPIRE armed exactly once, on the first hit, with the window length.
    assert ("expire", 45) in calls
    assert sum(1 for c in calls if c[0] == "expire") == 1


# ---------------------------------------------------------------------------
# Route wiring — public routes carry the limiter; authed routes do not (P4)
# ---------------------------------------------------------------------------


def _route_dependency_names(app_routes: object, path_suffix: str, method: str = "GET") -> set[str]:
    """Collect the names of every dependency callable resolved for a route.

    Walks the route's dependant tree so a dependency declared via ``Annotated``
    parameter is found regardless of nesting.
    """
    from fastapi.routing import APIRoute

    names: set[str] = set()
    for route in app_routes:  # type: ignore[attr-defined]
        if not isinstance(route, APIRoute):
            continue
        if not route.path.endswith(path_suffix) or method not in route.methods:
            continue

        def _walk(dep: object) -> None:
            call = getattr(dep, "call", None)
            if call is not None and getattr(call, "__name__", None):
                names.add(call.__name__)
            for sub in getattr(dep, "dependencies", []):
                _walk(sub)

        _walk(route.dependant)
    return names


def test_public_signal_routes_have_rate_limiter() -> None:
    from civicsignals_api.main import app as main_app

    for suffix in ("/signals/{signal_id}/public", "/signals/{signal_id}/sources"):
        names = _route_dependency_names(main_app.routes, suffix)
        assert "public_signal_limiter" in names, f"{suffix} missing limiter; deps={names}"


def test_public_directory_routes_have_rate_limiter() -> None:
    from civicsignals_api.main import app as main_app

    # list (/entities), get (/entities/{entity_id}), children
    for suffix in ("/entities", "/entities/{entity_id}", "/entities/{entity_id}/children"):
        names = _route_dependency_names(main_app.routes, suffix)
        assert "public_directory_limiter" in names, f"{suffix} missing limiter; deps={names}"


def test_authenticated_feed_route_is_not_rate_limited() -> None:
    """The workspace-scoped /feed must NOT carry the public limiter (P4 scope)."""
    from civicsignals_api.main import app as main_app

    names = _route_dependency_names(main_app.routes, "/signals/feed")
    assert "public_signal_limiter" not in names
    assert "public_directory_limiter" not in names


def test_authenticated_admin_route_is_not_rate_limited() -> None:
    """Admin fuzzy-review routes are authenticated; the public limiter is not on them."""
    from civicsignals_api.main import app as main_app

    names = _route_dependency_names(main_app.routes, "/signals/fuzzy-reviews")
    assert "public_signal_limiter" not in names
