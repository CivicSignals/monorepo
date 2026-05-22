"""Bounded-queue backpressure for the ingestion scheduler (D15; doc 18 §6.4).

Per-queue soft + hard depth thresholds with hysteresis:

* **Hard threshold** — when the ``ingest`` queue depth reaches or exceeds this
  value the scheduler pauses new recipe dispatches entirely.  In-flight crawls
  are never killed; only *new* enqueues are blocked.
* **Soft threshold** — once paused, the scheduler resumes only when the queue
  drains *below* the soft threshold (soft < hard).  The gap between soft and
  hard is the hysteresis band that prevents flapping: the scheduler won't
  pause-then-immediately-resume because a single crawl completes and pops the
  depth just below the hard limit.

Queue-depth probe
-----------------
Celery uses Redis lists as queues.  The ``ingest`` queue is the Redis list key
``ingest`` (Celery's default naming convention for non-``kombu``-configured
queues).  The depth is probed via Redis ``LLEN``.

The probe is hidden behind a :class:`QueueDepthProbe` protocol so tests can
inject a fake without a live Redis.  Production code constructs a
:class:`RedisQueueDepthProbe` from the settings ``redis_url`` (the general
lock/cache DB the scheduler already uses for leader-election).

Hysteresis state
----------------
The paused/running state is tracked in a :class:`BackpressureGate` instance.
That instance is module-level (a singleton per Python process) so the
hysteresis is correctly maintained across consecutive beat ticks within the
same scheduler process.  The gate is reset to "running" at process start so a
fresh deployment always starts accepting work immediately.

The :func:`ingest_queue_backpressure_gate` factory returns the shared singleton
when called without arguments (normal scheduler path) and accepts an injected
gate for tests.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Queue-depth probe abstraction
# ---------------------------------------------------------------------------


@runtime_checkable
class QueueDepthProbe(Protocol):
    """Return the current depth (number of pending tasks) of a named queue."""

    def depth(self, queue_name: str) -> int: ...


class RedisQueueDepthProbe:
    """Probe queue depth via Redis ``LLEN`` (Celery's default broker storage).

    Celery stores pending tasks for the ``ingest`` queue under the Redis list
    key ``ingest`` (default; no namespace prefix when the broker is configured
    with a plain ``redis://`` URL and no ``task_default_queue`` override).

    A separate ``redis.Redis`` client is created lazily on first use (not in
    ``__init__``) so importing this module doesn't require Redis to be
    reachable at module-import time (important for tests and the ``api``
    process that never touches the queue probe).
    """

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client: Any = None
        self._lock = threading.Lock()

    def _get_client(self) -> Any:
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import redis

                    self._client = redis.Redis.from_url(self._url, decode_responses=True)
        return self._client

    def depth(self, queue_name: str) -> int:
        """Return ``LLEN queue_name`` from Redis (0 if key is absent or error)."""
        import redis as redis_lib

        client = self._get_client()
        try:
            result = client.llen(queue_name)
            return int(result) if result is not None else 0
        except redis_lib.RedisError:
            # Treat a probe failure as "no depth info available" — fail open
            # (don't block the scheduler when Redis is unreachable transiently).
            log.warning("ingestion.backpressure.probe_error", queue_name=queue_name)
            return 0


# ---------------------------------------------------------------------------
# Per-queue threshold configuration
# ---------------------------------------------------------------------------

# Default thresholds for the ``ingest`` queue.  Sized for a single-node VPS
# deployment (doc 06 §10 Phase 0) where a large backlog means the workers are
# falling behind and new crawls would only worsen memory pressure.
#
# Override via environment:
#   INGEST_QUEUE_SOFT_THRESHOLD=300
#   INGEST_QUEUE_HARD_THRESHOLD=500
#
# The settings fields are on :class:`civicsignals_api.config.Settings`; see
# :func:`get_thresholds` below.

_DEFAULT_INGEST_SOFT = 500
_DEFAULT_INGEST_HARD = 1000

INGEST_QUEUE_NAME = "ingest"


def get_thresholds(queue_name: str = INGEST_QUEUE_NAME) -> tuple[int, int]:
    """Return ``(soft, hard)`` for *queue_name* from settings.

    Currently only the ``ingest`` queue has per-queue thresholds; the helper
    is structured for future per-queue extension.  The soft threshold is
    clamped to be strictly less than the hard threshold to prevent a degenerate
    configuration where hysteresis is impossible.
    """
    from civicsignals_api.config import get_settings

    s = get_settings()
    if queue_name == INGEST_QUEUE_NAME:
        hard = s.ingest_queue_hard_threshold
        soft = min(s.ingest_queue_soft_threshold, hard - 1)
    else:
        # Unmapped queue: use conservative defaults (fail-safe).
        hard = _DEFAULT_INGEST_HARD
        soft = _DEFAULT_INGEST_SOFT
    return soft, hard


# ---------------------------------------------------------------------------
# Hysteresis gate
# ---------------------------------------------------------------------------


class BackpressureGate:
    """Stateful gate implementing soft/hard hysteresis for one queue.

    Thread-safe (the scheduler process is single-threaded for beat, but the
    gate is accessed from both the beat task and potentially a monitoring path,
    so we guard it with a lock for correctness).

    State machine::

        RUNNING ──[depth >= hard]──► PAUSED
        PAUSED  ──[depth < soft]───► RUNNING

    The gate starts in RUNNING state.  Transitions log a structured event
    with the queue name, current depth, and the threshold crossed.
    """

    def __init__(self, queue_name: str = INGEST_QUEUE_NAME) -> None:
        self._queue_name = queue_name
        self._paused = False
        self._lock = threading.Lock()

    @property
    def is_paused(self) -> bool:
        """True when the gate is currently in the paused (backpressure) state."""
        with self._lock:
            return self._paused

    def has_headroom(self, probe: QueueDepthProbe) -> bool:
        """Probe the queue and return whether the scheduler should enqueue.

        Applies hysteresis:
        - If currently RUNNING: pause when ``depth >= hard``.
        - If currently PAUSED: resume only when ``depth < soft``.
        - Otherwise preserve current state.

        Returns True (has headroom) when the scheduler may enqueue, False when
        backpressure is engaged and the scheduler must skip this tick.
        """
        try:
            depth = probe.depth(self._queue_name)
        except Exception:
            # Probe failure (e.g. Redis unreachable transiently) — fail open:
            # do not block the scheduler when we can't measure the queue.
            # We return True unconditionally here (not checking paused state)
            # because we have no depth info to act on.
            log.warning(
                "ingestion.backpressure.probe_error",
                queue=self._queue_name,
            )
            return True

        soft, hard = get_thresholds(self._queue_name)

        with self._lock:
            if not self._paused:
                if depth >= hard:
                    self._paused = True
                    log.warning(
                        "ingestion.backpressure.engaged",
                        queue=self._queue_name,
                        depth=depth,
                        hard_threshold=hard,
                    )
            else:
                if depth < soft:
                    self._paused = False
                    log.info(
                        "ingestion.backpressure.resumed",
                        queue=self._queue_name,
                        depth=depth,
                        soft_threshold=soft,
                    )

            return not self._paused

    def reset(self) -> None:
        """Force the gate back to RUNNING state (for testing / fresh deployments)."""
        with self._lock:
            self._paused = False


# ---------------------------------------------------------------------------
# Module-level singleton (one gate per process, one per managed queue)
# ---------------------------------------------------------------------------

_gate_lock = threading.Lock()
_ingest_gate: BackpressureGate | None = None


def _get_ingest_gate() -> BackpressureGate:
    """Return the per-process ``ingest`` queue backpressure gate (lazy init)."""
    global _ingest_gate
    if _ingest_gate is None:
        with _gate_lock:
            if _ingest_gate is None:
                _ingest_gate = BackpressureGate(INGEST_QUEUE_NAME)
    return _ingest_gate


def check_ingest_queue_headroom(
    *,
    probe: QueueDepthProbe | None = None,
    gate: BackpressureGate | None = None,
) -> bool:
    """Return True when the scheduler may enqueue new recipe runs.

    This is the main entry point called by the D4 dispatcher.  Both
    *probe* and *gate* are injectable for testing:

    - *probe* defaults to a :class:`RedisQueueDepthProbe` reading ``redis_url``
      from the app settings.
    - *gate* defaults to the module-level singleton (persists across ticks).

    Injecting a fake *probe* lets unit tests drive any queue depth without a
    live Redis.  Injecting a fresh *gate* gives tests a clean hysteresis state.
    """
    if gate is None:
        gate = _get_ingest_gate()
    if probe is None:
        from civicsignals_api.config import get_settings

        probe = RedisQueueDepthProbe(get_settings().redis_url)

    return gate.has_headroom(probe)


__all__ = [
    "INGEST_QUEUE_NAME",
    "BackpressureGate",
    "QueueDepthProbe",
    "RedisQueueDepthProbe",
    "check_ingest_queue_headroom",
    "get_thresholds",
]
