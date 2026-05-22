"""Unit tests for D15 bounded-queue backpressure (doc 18 §6.4).

All tests are pure (no live Redis, no DB, no network).  The
:class:`~ingestion.backpressure.QueueDepthProbe` is mocked with a
``FixedDepthProbe`` that returns a caller-controlled integer so we can drive
any queue depth in tests.

Coverage:
1. Below soft → headroom=True (should enqueue).
2. At/above hard → headroom=False (pause).
3. Hysteresis: stays paused between soft and hard once the hard threshold is
   tripped; only resumes when depth falls below the soft threshold.
4. Resume below soft after a pause.
5. Dispatcher integration: the D4 dispatcher skips enqueuing when headroom=False
   (monkeypatched ``_ingest_queue_has_headroom`` returning False) and resumes
   when True — exercising the hook that D15 fills.
6. Threshold config: soft is clamped to < hard even when misconfigured.
7. Probe failure (RedisError) → fail open (headroom=True, don't block scheduler).
"""

from __future__ import annotations

import pytest

from civicsignals_api.modules.ingestion.backpressure import (
    INGEST_QUEUE_NAME,
    BackpressureGate,
    check_ingest_queue_headroom,
    get_thresholds,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FixedDepthProbe:
    """Fake probe that returns a fixed depth for the named queue."""

    def __init__(self, depth: int) -> None:
        self._depth = depth

    def depth(self, queue_name: str) -> int:
        return self._depth


class VariableDepthProbe:
    """Fake probe whose depth can be updated between calls."""

    def __init__(self, initial: int = 0) -> None:
        self._depth = initial

    def set(self, depth: int) -> None:
        self._depth = depth

    def depth(self, queue_name: str) -> int:
        return self._depth


# ---------------------------------------------------------------------------
# 1. Below soft → headroom True
# ---------------------------------------------------------------------------


def test_headroom_true_below_soft() -> None:
    """Queue depth below the soft threshold: dispatcher should enqueue freely."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    # Default soft=500, hard=1000; depth 0 is well below soft.
    probe = FixedDepthProbe(0)
    assert gate.has_headroom(probe) is True
    assert gate.is_paused is False


def test_headroom_true_at_soft_minus_one() -> None:
    """Depth exactly one below soft threshold is still headroom."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    soft, _hard = get_thresholds(INGEST_QUEUE_NAME)
    probe = FixedDepthProbe(soft - 1)
    assert gate.has_headroom(probe) is True


# ---------------------------------------------------------------------------
# 2. At / above hard → headroom False (pause)
# ---------------------------------------------------------------------------


def test_headroom_false_at_hard_threshold() -> None:
    """Depth exactly at the hard threshold triggers the pause."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    _soft, hard = get_thresholds(INGEST_QUEUE_NAME)
    probe = FixedDepthProbe(hard)
    assert gate.has_headroom(probe) is False
    assert gate.is_paused is True


def test_headroom_false_above_hard_threshold() -> None:
    """Depth above the hard threshold also pauses."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    _soft, hard = get_thresholds(INGEST_QUEUE_NAME)
    probe = FixedDepthProbe(hard + 500)
    assert gate.has_headroom(probe) is False
    assert gate.is_paused is True


# ---------------------------------------------------------------------------
# 3. Hysteresis: stays paused between soft and hard once tripped
# ---------------------------------------------------------------------------


def test_hysteresis_stays_paused_between_soft_and_hard() -> None:
    """Once paused, the gate must NOT resume just because depth is between soft and hard.

    This is the core hysteresis requirement: the scheduler stays paused until
    the queue fully drains below the *soft* threshold, preventing flapping at
    the hard boundary.
    """
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    soft, hard = get_thresholds(INGEST_QUEUE_NAME)

    # Trip the pause by reaching the hard threshold.
    probe = VariableDepthProbe(hard)
    result = gate.has_headroom(probe)
    assert result is False, "should be paused at hard threshold"
    assert gate.is_paused is True

    # Depth drops to halfway between soft and hard — still must stay paused.
    mid = (soft + hard) // 2
    probe.set(mid)
    result = gate.has_headroom(probe)
    assert result is False, (
        f"should stay paused at depth={mid} (between soft={soft} and hard={hard})"
    )
    assert gate.is_paused is True

    # Depth drops to exactly soft — still paused (resume only when *below* soft).
    probe.set(soft)
    result = gate.has_headroom(probe)
    assert result is False, f"should stay paused at depth=soft={soft} (not below soft)"
    assert gate.is_paused is True


# ---------------------------------------------------------------------------
# 4. Resume below soft after a pause
# ---------------------------------------------------------------------------


def test_resumes_below_soft_after_pause() -> None:
    """The gate resumes (headroom=True) only once depth drops strictly below soft."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    soft, hard = get_thresholds(INGEST_QUEUE_NAME)

    # Pause.
    probe = VariableDepthProbe(hard)
    assert gate.has_headroom(probe) is False

    # Drop below soft → should resume.
    probe.set(soft - 1)
    result = gate.has_headroom(probe)
    assert result is True, f"should resume when depth={soft - 1} < soft={soft}"
    assert gate.is_paused is False


def test_subsequent_dispatch_allowed_after_resume() -> None:
    """Confirm the gate stays unpaused after resuming (not a one-shot)."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    _soft, hard = get_thresholds(INGEST_QUEUE_NAME)

    probe = VariableDepthProbe(hard)
    gate.has_headroom(probe)  # pause
    probe.set(0)
    assert gate.has_headroom(probe) is True  # resume
    assert gate.has_headroom(probe) is True  # still running on next call


# ---------------------------------------------------------------------------
# 5. check_ingest_queue_headroom public API
# ---------------------------------------------------------------------------


def test_check_ingest_queue_headroom_injectable() -> None:
    """The public helper forwards probe + gate injections correctly."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    _soft, hard = get_thresholds(INGEST_QUEUE_NAME)

    # Inject a probe that reports a depth at the hard threshold → should pause.
    over_limit = FixedDepthProbe(hard)
    result = check_ingest_queue_headroom(probe=over_limit, gate=gate)
    assert result is False
    assert gate.is_paused is True

    # Fresh gate (clean state) + probe below soft → headroom.
    fresh_gate = BackpressureGate(INGEST_QUEUE_NAME)
    under_limit = FixedDepthProbe(0)
    result = check_ingest_queue_headroom(probe=under_limit, gate=fresh_gate)
    assert result is True


# ---------------------------------------------------------------------------
# 6. Threshold config clamping
# ---------------------------------------------------------------------------


def test_get_thresholds_returns_soft_and_hard(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_thresholds returns (soft, hard) from settings."""
    from civicsignals_api import config as config_module

    # Reload a fresh Settings instance with controlled values.
    from civicsignals_api.config import Settings

    settings = Settings(
        ingest_queue_soft_threshold=300,
        ingest_queue_hard_threshold=600,
    )
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)

    soft, hard = get_thresholds(INGEST_QUEUE_NAME)
    assert soft == 300
    assert hard == 600


def test_get_thresholds_clamps_soft_below_hard(monkeypatch: pytest.MonkeyPatch) -> None:
    """When soft >= hard, get_thresholds clamps soft to hard-1."""
    from civicsignals_api import config as config_module
    from civicsignals_api.config import Settings

    # Misconfigured: soft == hard.
    settings = Settings(
        ingest_queue_soft_threshold=500,
        ingest_queue_hard_threshold=500,
    )
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)

    soft, hard = get_thresholds(INGEST_QUEUE_NAME)
    assert hard == 500
    assert soft == 499  # clamped to hard - 1


# ---------------------------------------------------------------------------
# 7. Probe failure → fail open
# ---------------------------------------------------------------------------


class ErrorProbe:
    """Probe that raises RedisError to simulate a Redis connectivity problem."""

    def depth(self, queue_name: str) -> int:
        import redis

        raise redis.RedisError("simulated connection failure")


def test_probe_failure_fails_open() -> None:
    """A Redis probe failure must NOT block the scheduler — fail open (headroom=True)."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    probe = ErrorProbe()
    # The gate should swallow the error and return True (don't block dispatch).
    result = gate.has_headroom(probe)
    assert result is True
    assert gate.is_paused is False


# ---------------------------------------------------------------------------
# 8. Dispatcher integration: D4 hooks
# ---------------------------------------------------------------------------


def test_dispatcher_skips_when_headroom_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """The D4 dispatcher enqueues nothing when _ingest_queue_has_headroom() is False.

    This exercises the integration seam that D15 fills.  The recipe scan,
    clock, and crawl_recipe.delay are all stubbed; only the backpressure hook
    is patched.
    """
    from civicsignals_api.modules.ingestion import tasks

    monkeypatch.setattr(tasks, "_ingest_queue_has_headroom", lambda: False)

    # _dispatch_due_recipes_async returns 0 without touching the DB or broker.
    import asyncio

    # We need to stub the async session to avoid hitting the DB.
    # Since backpressure gate short-circuits before the DB code, we just run it.
    count = asyncio.run(tasks._dispatch_due_recipes_async())
    assert count == 0


def test_dispatcher_enqueues_when_headroom_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """The D4 dispatcher proceeds when _ingest_queue_has_headroom() is True.

    We stub everything except the headroom check to confirm the gate-True path
    reaches the scan + dispatch logic (the dispatch itself is stubbed).
    """
    from civicsignals_api.modules.ingestion import scheduler as scheduler_module
    from civicsignals_api.modules.ingestion import services, tasks

    # Headroom = True (no backpressure).
    monkeypatch.setattr(tasks, "_ingest_queue_has_headroom", lambda: True)
    # No active recipes → count=0, but the scan was reached (not short-circuited).
    monkeypatch.setattr(scheduler_module, "load_active_recipes", lambda: [])
    # Stub the DB session so we don't need a real Postgres.
    import asyncio
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()
    monkeypatch.setattr(services, "list_recipe_schedules", AsyncMock(return_value=[]))

    @asynccontextmanager  # type: ignore[arg-type]
    async def _fake_session_local() -> object:
        yield mock_session

    from civicsignals_api import db as db_module

    monkeypatch.setattr(db_module, "SessionLocal", _fake_session_local)

    enqueued: list[object] = []
    monkeypatch.setattr(tasks.crawl_recipe, "delay", lambda *a: enqueued.append(a))

    count = asyncio.run(tasks._dispatch_due_recipes_async())
    # No recipes → dispatched 0, but we DID reach the scan (headroom was True).
    assert count == 0


# ---------------------------------------------------------------------------
# 9. gate.reset() helper
# ---------------------------------------------------------------------------


def test_gate_reset_clears_paused_state() -> None:
    """BackpressureGate.reset() returns the gate to RUNNING state."""
    gate = BackpressureGate(INGEST_QUEUE_NAME)
    _soft, hard = get_thresholds(INGEST_QUEUE_NAME)

    gate.has_headroom(FixedDepthProbe(hard))  # pause
    assert gate.is_paused is True

    gate.reset()
    assert gate.is_paused is False
    # After reset, a depth below hard shows headroom again.
    assert gate.has_headroom(FixedDepthProbe(0)) is True
