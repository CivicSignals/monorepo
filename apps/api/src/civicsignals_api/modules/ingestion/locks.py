"""Redis-backed distributed locks for the ingestion scheduler (D4).

Two locks live here (doc 18 §6.1, doc 06 §8):

* :class:`RedisLock` — a single-holder lock backed by ``SET key token NX PX ttl``.
  It powers both the **scheduler leader election** (only one ``scheduler``
  container actually schedules, even if several run) and the **per-recipe run
  lock** (a recipe isn't crawled concurrently / re-entered while a prior run is
  still in flight).
* :func:`leader_lock` / :func:`recipe_run_lock` — thin context-manager helpers
  that build the two lock kinds with the right key namespaces + TTLs.

Why a token + Lua release (not a bare ``DEL``): a holder must only release a lock
it *still* owns. If the holder stalled long enough for the TTL to expire and a new
holder acquired it, a naive ``DEL`` would delete the *new* holder's lock. The
release is therefore a compare-and-delete Lua script keyed on the random token the
acquirer wrote, so it is a no-op unless we still hold it.

The Redis client is injected (a small :class:`RedisClient` Protocol) so tests pass
an in-memory fake — no live Redis needed and no network in the unit suite.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Protocol, runtime_checkable

# Default lock namespaces + TTLs (seconds).
LEADER_LOCK_KEY = "civicsignals:scheduler:leader"
# The beat tick is ~1 minute; the leader lease must outlive a tick (so a single
# slow tick doesn't drop leadership mid-run) but be short enough that a crashed
# leader is replaced within a couple of ticks. 90s gives ~1.5 ticks of slack.
LEADER_LOCK_TTL_SECONDS = 90.0

RECIPE_RUN_LOCK_PREFIX = "civicsignals:ingest:recipe-run:"
# A crawl run can take minutes (discover -> fetch with politeness windows -> …),
# so the per-recipe run lock leases longer than the leader lock. It is released
# explicitly when the run finishes; the TTL is the safety net for a worker that
# dies mid-crawl so the recipe isn't wedged forever (doc 18 §6.4).
RECIPE_RUN_LOCK_TTL_SECONDS = 3600.0

# Compare-and-delete: only release if the stored value is still our token.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


@runtime_checkable
class RedisClient(Protocol):
    """The minimal Redis surface the locks need (so tests can fake it).

    Matches ``redis.Redis`` for the calls used: ``set`` with ``nx``/``px`` and
    ``eval``. ``decode_responses`` may be on or off — we only compare bytes/str
    we wrote ourselves, and the Lua compare-and-delete handles the equality.
    """

    def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = ...,
        px: int | None = ...,
    ) -> bool | None: ...

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object: ...


def get_redis_client() -> RedisClient:
    """Build a Redis client from settings (the scheduler/worker process uses this).

    Lazily imports ``redis`` and connects to ``settings.redis_url`` (the general
    cache/lock DB — *not* the Celery broker/result DBs, doc 06 §8). ``decode_
    responses=True`` so the token round-trips as ``str`` for the Lua comparison.

    ``redis.Redis`` types ``set``/``eval`` with an ``Awaitable | Any`` return (the
    sync/async client share one stub), which doesn't structurally match the narrow
    sync :class:`RedisClient` Protocol; the synchronous client returns the plain
    values at runtime, so we cast to the Protocol the locks rely on.
    """
    from typing import cast

    import redis

    from civicsignals_api.config import get_settings

    client = redis.Redis.from_url(get_settings().redis_url, decode_responses=True)
    return cast(RedisClient, client)


class RedisLock:
    """A single-holder lock backed by ``SET key token NX PX``.

    Acquire writes a random token under ``key`` only if absent (``NX``) with a
    millisecond TTL (``PX``); the boolean result tells us if we got it. Release is
    a compare-and-delete on the token so we never drop a lock a *later* holder
    acquired after our TTL lapsed. Re-entrant only within the same instance via
    :meth:`acquired` — it does not block/retry, mirroring beat's "skip this tick if
    someone else holds it" model (doc 18 §6.1).
    """

    def __init__(self, client: RedisClient, key: str, *, ttl_seconds: float) -> None:
        self._client = client
        self._key = key
        self._ttl_ms = max(1, int(ttl_seconds * 1000))
        self._token: str | None = None

    @property
    def key(self) -> str:
        return self._key

    @property
    def token(self) -> str | None:
        return self._token

    def acquire(self) -> bool:
        """Try to take the lock once (non-blocking). Returns True iff acquired."""
        token = secrets.token_hex(16)
        got = self._client.set(self._key, token, nx=True, px=self._ttl_ms)
        if got:
            self._token = token
            return True
        return False

    def release(self) -> bool:
        """Release the lock iff we still hold it (compare-and-delete). Idempotent."""
        if self._token is None:
            return False
        result = self._client.eval(_RELEASE_SCRIPT, 1, self._key, self._token)
        released = bool(result)
        self._token = None
        return released

    def acquired(self) -> bool:
        return self._token is not None


@contextmanager
def _guard(lock: RedisLock) -> Iterator[bool]:
    """Yield whether ``lock`` was acquired; always release on the way out."""
    got = lock.acquire()
    try:
        yield got
    finally:
        if got:
            lock.release()


@contextmanager
def leader_lock(
    client: RedisClient,
    *,
    key: str = LEADER_LOCK_KEY,
    ttl_seconds: float = LEADER_LOCK_TTL_SECONDS,
) -> Iterator[bool]:
    """Scheduler leader election (doc 18 §6.1): yield True iff this process is leader.

    Wrap the dispatcher body so only the one process that holds the lease does the
    scheduling work; the others skip the tick. Acquired non-blocking and released
    at the end of the tick so leadership naturally migrates if the holder dies (the
    TTL is the backstop). The lock is *not* held across ticks — each tick re-elects,
    which keeps the design stateless and crash-tolerant.
    """
    with _guard(RedisLock(client, key, ttl_seconds=ttl_seconds)) as got:
        yield got


@contextmanager
def recipe_run_lock(
    client: RedisClient,
    recipe_id: str,
    *,
    ttl_seconds: float = RECIPE_RUN_LOCK_TTL_SECONDS,
) -> Iterator[bool]:
    """Per-recipe run lock (doc 18 §6.4): yield True iff no run is already in flight.

    Prevents a recipe being crawled concurrently / re-entered while a prior run is
    still running (a slow crawl outliving its cadence, or a duplicate dispatch).
    The TTL is the safety net for a worker that dies mid-crawl so the recipe isn't
    wedged; the normal path releases explicitly when the run finishes.
    """
    key = f"{RECIPE_RUN_LOCK_PREFIX}{recipe_id}"
    with _guard(RedisLock(client, key, ttl_seconds=ttl_seconds)) as got:
        yield got


def now_monotonic() -> float:
    """Monotonic clock seam (kept here so lock-timing tests don't import time)."""
    return time.monotonic()


__all__ = [
    "LEADER_LOCK_KEY",
    "LEADER_LOCK_TTL_SECONDS",
    "RECIPE_RUN_LOCK_PREFIX",
    "RECIPE_RUN_LOCK_TTL_SECONDS",
    "RedisClient",
    "RedisLock",
    "get_redis_client",
    "leader_lock",
    "now_monotonic",
    "recipe_run_lock",
]
