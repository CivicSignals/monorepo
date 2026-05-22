"""Unit tests for the Redis-backed scheduler locks (D4; doc 18 §6.1, §6.4).

Uses a tiny in-memory fake implementing the slice of the Redis API the locks
use (``SET key value NX PX`` + the compare-and-delete Lua ``eval``), so the leader
lock and the per-recipe run lock are exercised without a live Redis.
"""

from __future__ import annotations

from civicsignals_api.modules.ingestion.locks import (
    RECIPE_RUN_LOCK_PREFIX,
    RedisLock,
    leader_lock,
    recipe_run_lock,
)


class FakeRedis:
    """In-memory stand-in for ``redis.Redis`` covering ``set`` (NX/PX) + ``eval``.

    No real expiry timing — TTL bookkeeping isn't needed for the lock-semantics
    tests (we test acquire-once-then-blocked and compare-and-delete release). A
    key can be force-expired via :meth:`expire_now` to simulate a TTL lapse.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(
        self,
        name: str,
        value: str,
        *,
        nx: bool = False,
        px: int | None = None,
    ) -> bool | None:
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> object:
        # Only the compare-and-delete release script is used. Emulate it directly.
        key = keys_and_args[0]
        token = keys_and_args[1]
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0

    def incr(self, name: str, amount: int = 1) -> int:
        # Part of the RedisClient Protocol (used by the P4 rate limiter, not the
        # locks). Implemented so FakeRedis still satisfies the Protocol under mypy.
        value = int(self.store.get(name, "0")) + amount
        self.store[name] = str(value)
        return value

    def expire(self, name: str, time: int) -> bool:
        # Protocol member (P4 rate limiter). No real TTL bookkeeping is needed for
        # the lock-semantics tests; report success iff the key exists.
        return name in self.store

    def expire_now(self, name: str) -> None:
        self.store.pop(name, None)


def test_redis_lock_acquire_then_blocks_second_holder() -> None:
    client = FakeRedis()
    a = RedisLock(client, "k", ttl_seconds=60)
    b = RedisLock(client, "k", ttl_seconds=60)
    assert a.acquire() is True
    assert a.acquired() is True
    # A second contender can't take a held lock.
    assert b.acquire() is False
    assert b.acquired() is False


def test_redis_lock_release_then_reacquire() -> None:
    client = FakeRedis()
    a = RedisLock(client, "k", ttl_seconds=60)
    b = RedisLock(client, "k", ttl_seconds=60)
    assert a.acquire() is True
    assert a.release() is True
    assert a.acquired() is False
    # Released -> a new holder can now take it.
    assert b.acquire() is True


def test_release_is_idempotent_and_token_scoped() -> None:
    client = FakeRedis()
    a = RedisLock(client, "k", ttl_seconds=60)
    assert a.acquire() is True
    assert a.release() is True
    # Releasing again is a harmless no-op (we no longer hold a token).
    assert a.release() is False


def test_release_does_not_drop_a_later_holders_lock() -> None:
    """A stale holder must not delete a lock a newer holder acquired (compare-and-delete)."""
    client = FakeRedis()
    a = RedisLock(client, "k", ttl_seconds=60)
    b = RedisLock(client, "k", ttl_seconds=60)
    assert a.acquire() is True
    # Simulate a's TTL lapsing and b grabbing the lock.
    client.expire_now("k")
    assert b.acquire() is True
    # a (still thinking it holds the lock) releases — must NOT remove b's lock.
    assert a.release() is False
    assert client.store.get("k") == b.token


def test_leader_lock_only_one_acquires() -> None:
    client = FakeRedis()
    with leader_lock(client) as first:
        assert first is True
        # A second concurrent leader attempt while the first is held -> not leader.
        with leader_lock(client) as second:
            assert second is False
    # After the first leader released, a fresh election succeeds again.
    with leader_lock(client) as third:
        assert third is True


def test_recipe_run_lock_prevents_concurrent_runs() -> None:
    client = FakeRedis()
    with recipe_run_lock(client, "wa-state-webs") as got:
        assert got is True
        assert f"{RECIPE_RUN_LOCK_PREFIX}wa-state-webs" in client.store
        # A second crawl of the same recipe while one is in flight is blocked.
        with recipe_run_lock(client, "wa-state-webs") as reentrant:
            assert reentrant is False
        # A *different* recipe is independent.
        with recipe_run_lock(client, "other-recipe") as other:
            assert other is True
    # Lock released when the run finishes.
    assert f"{RECIPE_RUN_LOCK_PREFIX}wa-state-webs" not in client.store
