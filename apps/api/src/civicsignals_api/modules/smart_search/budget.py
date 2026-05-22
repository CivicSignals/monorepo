"""Per-workspace daily Smart Search LLM budget enforcement (I5).

Design
------
The budget is a **soft cap** on LLM-assisted smart-search calls per workspace
per calendar day (UTC).  When a workspace reaches the cap the endpoint
gracefully falls back to keyword-only retrieval (BM25/FTS + filters, skipping
the LLM rewrite and summary) and sets ``budget_exhausted=True`` in the response
— it never returns an error.

Storage
-------
A lightweight in-process counter keyed by ``(workspace_id, UTC-date)`` suffices
for MVP (the same approach as :class:`~civicsignals_api.llm_gateway.accounting.InMemoryTokenAccountant`).
The counter is concurrency-safe (a threading lock) and automatically expires:
entries from a previous UTC day are dropped on first access so memory stays
bounded without a background job.

A per-process counter means that in a multi-process / multi-worker deployment
(the default: uvicorn workers + Celery workers run as separate OS processes)
each *api* worker keeps its own counter and the aggregate across workers may
exceed the per-day cap before any single one trips.  For MVP this is the
intentional trade-off — the budget is a *soft* cap, not a hard billing gate.
TODO N3: swap to a Redis counter (``INCR`` + ``EXPIREAT``) for cross-process
precision when N3's persistent metering lands.

Configuration
-------------
``SMART_SEARCH_DAILY_LLM_CALL_LIMIT`` (env) / ``smart_search_daily_llm_call_limit``
(Settings) — integer, default 50 calls per workspace per day.  Set to 0 to
disable the budget entirely (all requests go through the LLM-assisted path).
"""

from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict

import structlog

log = structlog.get_logger(__name__)

# Sentinel: no cap enforced.
UNLIMITED: int = 0


class DailyBudgetTracker:
    """Thread-safe in-process per-workspace daily call counter.

    The counter resets automatically each UTC calendar day — no cron job needed.
    An instance is typically held as a module-level singleton (see
    :func:`get_budget_tracker`) so the same counter is shared across all
    requests in a single process.

    Args:
        daily_limit: Maximum LLM-assisted smart-search calls per workspace per
            UTC day.  ``0`` means unlimited (the budget is disabled); every
            :meth:`check_and_increment` call returns ``True``.
    """

    def __init__(self, daily_limit: int = 50) -> None:
        self._daily_limit = daily_limit
        self._lock = threading.Lock()
        # {workspace_id: {date: call_count}}
        self._counts: dict[str, dict[dt.date, int]] = defaultdict(lambda: defaultdict(int))

    @property
    def daily_limit(self) -> int:
        """The configured cap (0 = unlimited)."""
        return self._daily_limit

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_over_budget(self, workspace_id: str, *, today: dt.date | None = None) -> bool:
        """Return ``True`` if ``workspace_id`` has reached its daily cap.

        Does **not** increment the counter.  Use :meth:`check_and_increment`
        to atomically test-and-increment (preferred at call sites so a
        concurrent request does not sneak through between the check and the
        record).

        Args:
            workspace_id: The workspace to check.
            today: Override the current UTC date (testing only).
        """
        if self._daily_limit == UNLIMITED:
            return False
        today = today or dt.datetime.now(dt.UTC).date()
        with self._lock:
            count = self._counts[workspace_id].get(today, 0)
            return count >= self._daily_limit

    def increment(self, workspace_id: str, *, today: dt.date | None = None) -> int:
        """Increment the LLM call counter for ``workspace_id`` and return the new value.

        Old-day entries for this workspace are pruned on each call so memory is
        bounded to ``O(workspaces * 1)`` entries (only today's date is kept per
        workspace).  The pruning is cheap: the inner dict is typically 1 entry.

        Args:
            workspace_id: The workspace whose counter to increment.
            today: Override the current UTC date (testing only).

        Returns:
            The counter value *after* the increment.
        """
        today = today or dt.datetime.now(dt.UTC).date()
        with self._lock:
            ws_counts = self._counts[workspace_id]
            # Prune stale dates to keep memory bounded.
            stale = [d for d in ws_counts if d < today]
            for d in stale:
                del ws_counts[d]
            ws_counts[today] += 1
            new_count = ws_counts[today]
        return new_count

    def check_and_increment(self, workspace_id: str, *, today: dt.date | None = None) -> bool:
        """Atomically check the budget and, if under cap, increment the counter.

        Returns:
            ``True`` when the workspace is **under** budget (the LLM-assisted
            path is allowed) and the counter was incremented.
            ``False`` when the workspace is **at or over** the daily cap (fall
            back to keyword-only; the counter is **not** incremented — a
            keyword-only call does not consume budget).
        """
        if self._daily_limit == UNLIMITED:
            return True
        today = today or dt.datetime.now(dt.UTC).date()
        with self._lock:
            ws_counts = self._counts[workspace_id]
            # Prune stale dates.
            stale = [d for d in ws_counts if d < today]
            for d in stale:
                del ws_counts[d]
            count = ws_counts.get(today, 0)
            if count >= self._daily_limit:
                log.info(
                    "smart_search.budget.exhausted",
                    workspace_id=workspace_id,
                    count=count,
                    limit=self._daily_limit,
                )
                return False
            ws_counts[today] = count + 1
            return True

    def current_count(self, workspace_id: str, *, today: dt.date | None = None) -> int:
        """Return the current call count for ``workspace_id`` today (read-only).

        Used in tests and health/metrics endpoints; does not modify state.
        """
        today = today or dt.datetime.now(dt.UTC).date()
        with self._lock:
            return self._counts[workspace_id].get(today, 0)


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_tracker: DailyBudgetTracker | None = None
_tracker_lock = threading.Lock()


def get_budget_tracker() -> DailyBudgetTracker:
    """Return the process-level :class:`DailyBudgetTracker` singleton.

    Initialised lazily from :func:`~civicsignals_api.config.get_settings` on
    first call so unit tests that override settings see the right cap without
    pre-warming the singleton.  Production callers (FastAPI lifespan, services)
    use this to share one counter across all requests in a process.
    """
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                from civicsignals_api.config import get_settings

                limit = get_settings().smart_search_daily_llm_call_limit
                _tracker = DailyBudgetTracker(daily_limit=limit)
                log.info(
                    "smart_search.budget.init",
                    daily_limit=limit,
                )
    return _tracker


def _reset_singleton() -> None:
    """Reset the module-level singleton (testing only — do not call in prod)."""
    global _tracker
    with _tracker_lock:
        _tracker = None


__all__ = [
    "UNLIMITED",
    "DailyBudgetTracker",
    "get_budget_tracker",
]
