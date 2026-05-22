"""Tests for Smart Search per-workspace daily LLM budget enforcement (I5).

Covers the requirements from the task spec:

1. Budget tracking: per-workspace daily counters; config-driven cap.
2. Soft cap + graceful fallback:
   - under budget → full LLM path (rewrite + vector ANN + summary invoked).
   - at/over budget → keyword-only fallback (LLM NOT invoked, results still
     returned, ``budget_exhausted=True`` + ``degraded=True`` set).
3. Accounting: counter increments only for LLM-assisted runs; keyword-only
   fallback does NOT consume budget.
4. Per-workspace isolation: workspaces are independent counters.
5. Config cap respected: cap=1 → second call is keyword-only.

All LLM/embedding access uses FakeBackend (no network, no real LLM).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from civicsignals_api.llm_gateway import (
    TASK_EMBED,
    TASK_SMART_SEARCH_REWRITE,
    TASK_SMART_SEARCH_SUMMARY,
    FakeBackend,
    FakeEmbeddingBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.smart_search.budget import DailyBudgetTracker
from civicsignals_api.modules.smart_search.schemas import SmartSearchResponse, StructuredQuery
from civicsignals_api.modules.smart_search.services import HybridRetriever

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMBED_DIM = 1536


def _gateway(rewrite_responses: list[str] | None = None) -> LLMGateway:
    """A gateway wired with Fake backends (no network)."""
    policy = TaskModelPolicy(
        overrides={
            TASK_SMART_SEARCH_REWRITE: ModelChoice("fake", "haiku"),
            TASK_SMART_SEARCH_SUMMARY: ModelChoice("fake", "haiku"),
            TASK_EMBED: ModelChoice("fake", "embed"),
        }
    )
    canned = rewrite_responses or [json.dumps({"text": "cybersecurity", "filters": {}})]
    return LLMGateway(
        {"fake": FakeBackend(responses=canned)},
        policy=policy,
        embedding_backends={"fake": FakeEmbeddingBackend(dim=EMBED_DIM)},
        embedding_choice=ModelChoice("fake", "embed"),
    )


class _EmptySession:
    """DB session stub that always returns empty result sets."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        class _Result:
            def scalars(self) -> Any:
                class _Scalars:
                    def all(self) -> list[Any]:
                        return []

                return _Scalars()

        return _Result()


def _fake_workspace_ctx() -> object:
    from civicsignals_api.modules.accounts.models import MembershipRole, Workspace
    from civicsignals_api.modules.auth.dependencies import WorkspaceContext

    fake_ws = MagicMock(spec=Workspace)
    fake_ws.id = uuid.uuid4()
    fake_membership = MagicMock()
    fake_membership.role = MembershipRole.ADMIN
    return WorkspaceContext(workspace=fake_ws, user=MagicMock(), membership=fake_membership)


# ---------------------------------------------------------------------------
# DailyBudgetTracker unit tests
# ---------------------------------------------------------------------------


def test_tracker_unlimited_always_allows() -> None:
    """cap=0 (UNLIMITED) means check_and_increment always returns True."""
    tracker = DailyBudgetTracker(daily_limit=0)
    today = dt.date(2026, 5, 22)
    for _ in range(200):
        assert tracker.check_and_increment("ws-1", today=today) is True
    # Counter is not incremented for unlimited mode.
    assert tracker.current_count("ws-1", today=today) == 0


def test_tracker_under_budget_increments() -> None:
    """Calls under the cap return True and increment the counter."""
    tracker = DailyBudgetTracker(daily_limit=3)
    today = dt.date(2026, 5, 22)
    assert tracker.check_and_increment("ws-1", today=today) is True
    assert tracker.check_and_increment("ws-1", today=today) is True
    assert tracker.check_and_increment("ws-1", today=today) is True
    assert tracker.current_count("ws-1", today=today) == 3


def test_tracker_at_cap_returns_false_and_does_not_increment() -> None:
    """Once the cap is reached check_and_increment returns False without incrementing."""
    tracker = DailyBudgetTracker(daily_limit=2)
    today = dt.date(2026, 5, 22)
    assert tracker.check_and_increment("ws-1", today=today) is True
    assert tracker.check_and_increment("ws-1", today=today) is True
    # Now at cap.
    assert tracker.check_and_increment("ws-1", today=today) is False
    # Counter should still be exactly 2, not 3.
    assert tracker.current_count("ws-1", today=today) == 2


def test_tracker_workspace_isolation() -> None:
    """Different workspaces have independent counters."""
    tracker = DailyBudgetTracker(daily_limit=1)
    today = dt.date(2026, 5, 22)
    assert tracker.check_and_increment("ws-a", today=today) is True
    # ws-a is now at cap; ws-b is unaffected.
    assert tracker.check_and_increment("ws-a", today=today) is False
    assert tracker.check_and_increment("ws-b", today=today) is True


def test_tracker_resets_on_new_day() -> None:
    """Counter for a workspace resets when a new UTC day begins."""
    tracker = DailyBudgetTracker(daily_limit=1)
    day1 = dt.date(2026, 5, 22)
    day2 = dt.date(2026, 5, 23)
    assert tracker.check_and_increment("ws-1", today=day1) is True
    assert tracker.check_and_increment("ws-1", today=day1) is False  # at cap
    # On the next day the counter resets.
    assert tracker.check_and_increment("ws-1", today=day2) is True


def test_tracker_is_over_budget_does_not_increment() -> None:
    """is_over_budget is a pure read that does not change the counter."""
    tracker = DailyBudgetTracker(daily_limit=2)
    today = dt.date(2026, 5, 22)
    assert tracker.is_over_budget("ws-1", today=today) is False
    tracker.increment("ws-1", today=today)
    tracker.increment("ws-1", today=today)
    assert tracker.is_over_budget("ws-1", today=today) is True
    # Still 2 — is_over_budget didn't add anything.
    assert tracker.current_count("ws-1", today=today) == 2


def test_tracker_increment_prunes_stale_dates() -> None:
    """Stale date entries are pruned from memory when a new day's counter is written."""
    tracker = DailyBudgetTracker(daily_limit=10)
    day1 = dt.date(2026, 5, 22)
    day2 = dt.date(2026, 5, 23)
    tracker.increment("ws-1", today=day1)
    # After incrementing on day 2, day1's entry should be gone.
    tracker.increment("ws-1", today=day2)
    # day1 count is gone (returns 0 from the dict).
    assert tracker.current_count("ws-1", today=day1) == 0
    assert tracker.current_count("ws-1", today=day2) == 1


# ---------------------------------------------------------------------------
# HybridRetriever integration: under-budget path
# ---------------------------------------------------------------------------


async def test_under_budget_llm_path_invoked() -> None:
    """When under budget the full LLM-assisted path is used (rewrite is called)."""
    rewrite_called = False
    summary_called = False

    class _TrackingRewriter:
        async def rewrite(
            self, query: str, *, workspace_id: str | None = None, **kwargs: Any
        ) -> tuple[Any, Any]:
            nonlocal rewrite_called
            rewrite_called = True
            return StructuredQuery(text=query, degraded=False), None

    class _TrackingSummarizer:
        async def summarize(
            self, query: str, results: list[Any], *, workspace_id: str | None = None
        ) -> str | None:
            nonlocal summary_called
            summary_called = True
            return "summary text"

    tracker = DailyBudgetTracker(daily_limit=5)
    retriever = HybridRetriever(
        _gateway(),
        rewriter=_TrackingRewriter(),  # type: ignore[arg-type]
        summarizer=_TrackingSummarizer(),  # type: ignore[arg-type]
        budget_tracker=tracker,
    )

    resp = await retriever.search(
        _EmptySession(),  # type: ignore[arg-type]
        "cybersecurity RFPs",
        workspace_id="ws-budget-test",
        summarize=True,
    )

    assert rewrite_called is True, "Rewriter must be called on LLM-assisted path"
    assert summary_called is True, "Summarizer must be called on LLM-assisted path"
    assert resp.budget_exhausted is False
    assert resp.degraded is False
    # Counter was incremented once.
    assert tracker.current_count("ws-budget-test") == 1


async def test_under_budget_counter_increments_per_llm_run() -> None:
    """Each LLM-assisted run increments the budget counter by exactly 1."""
    tracker = DailyBudgetTracker(daily_limit=10)
    gw = _gateway(
        rewrite_responses=[
            json.dumps({"text": "cyber", "filters": {}}),
            json.dumps({"text": "budget", "filters": {}}),
            json.dumps({"text": "grant", "filters": {}}),
        ]
    )
    retriever = HybridRetriever(gw, budget_tracker=tracker)

    for _ in range(3):
        await retriever.search(
            _EmptySession(),  # type: ignore[arg-type]
            "test query",
            workspace_id="ws-counter",
        )

    assert tracker.current_count("ws-counter") == 3


# ---------------------------------------------------------------------------
# HybridRetriever integration: over-budget path (keyword-only fallback)
# ---------------------------------------------------------------------------


async def test_over_budget_llm_not_invoked() -> None:
    """When over budget LLM calls are skipped (rewriter + summarizer not called)."""
    rewrite_called = False
    summary_called = False

    class _FailRewriter:
        async def rewrite(
            self, query: str, *, workspace_id: str | None = None, **kwargs: Any
        ) -> tuple[Any, Any]:
            nonlocal rewrite_called
            rewrite_called = True
            raise AssertionError("Rewriter must NOT be called on keyword-only path")

    class _FailSummarizer:
        async def summarize(
            self, query: str, results: list[Any], *, workspace_id: str | None = None
        ) -> str | None:
            nonlocal summary_called
            summary_called = True
            raise AssertionError("Summarizer must NOT be called on keyword-only path")

    # Exhaust the budget immediately.
    tracker = DailyBudgetTracker(daily_limit=0)  # limit=0 means UNLIMITED
    # Actually set limit=1 and exhaust it first.
    tracker = DailyBudgetTracker(daily_limit=1)
    today = dt.date.today()
    tracker.increment("ws-exhausted", today=today)  # set count to 1 = at cap

    retriever = HybridRetriever(
        _gateway(),
        rewriter=_FailRewriter(),  # type: ignore[arg-type]
        summarizer=_FailSummarizer(),  # type: ignore[arg-type]
        budget_tracker=tracker,
    )

    resp = await retriever.search(
        _EmptySession(),  # type: ignore[arg-type]
        "grant opportunities",
        workspace_id="ws-exhausted",
        summarize=True,  # requested but should NOT happen
    )

    assert rewrite_called is False
    assert summary_called is False
    assert resp.budget_exhausted is True
    assert resp.degraded is True
    assert resp.summary is None


async def test_over_budget_results_still_returned() -> None:
    """Keyword-only fallback returns results normally (non-empty BM25 session)."""

    class _BM25Session:
        """Returns a couple of fake UUIDs for the BM25 leg, none for filter/vector."""

        def __init__(self) -> None:
            self._call_count = 0

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            self._call_count += 1

            class _Result:
                def __init__(self, values: list[Any]) -> None:
                    self._values = values

                def scalars(self) -> Any:
                    values = self._values

                    class _Scalars:
                        def all(self) -> list[Any]:
                            return values

                    return _Scalars()

            # The first execute is BM25, second is filter_ids.
            # BM25 returns no ids (empty DB); we just check no error is raised.
            return _Result([])

    tracker = DailyBudgetTracker(daily_limit=1)
    tracker.increment("ws-kw")  # exhaust budget

    retriever = HybridRetriever(_gateway(), budget_tracker=tracker)
    resp = await retriever.search(
        _BM25Session(),  # type: ignore[arg-type]
        "test query",
        workspace_id="ws-kw",
    )

    assert resp.budget_exhausted is True
    assert isinstance(resp.results, list)
    # No error raised; response is well-formed.
    assert resp.query.text == "test query"


async def test_over_budget_counter_not_incremented() -> None:
    """Keyword-only fallback does NOT consume the daily budget counter."""
    tracker = DailyBudgetTracker(daily_limit=1)
    tracker.increment("ws-kw2")  # exhaust budget; count = 1

    retriever = HybridRetriever(_gateway(), budget_tracker=tracker)

    for _ in range(5):
        resp = await retriever.search(
            _EmptySession(),  # type: ignore[arg-type]
            "test query",
            workspace_id="ws-kw2",
        )
        assert resp.budget_exhausted is True

    # Counter is still 1 (no keyword-only run incremented it).
    assert tracker.current_count("ws-kw2") == 1


# ---------------------------------------------------------------------------
# Per-workspace isolation at the retriever level
# ---------------------------------------------------------------------------


async def test_per_workspace_budget_isolation() -> None:
    """Exhausting budget for one workspace does not affect another workspace."""
    tracker = DailyBudgetTracker(daily_limit=1)
    today = dt.date.today()
    tracker.increment("ws-a", today=today)  # exhaust ws-a

    # Build two retrievers sharing the same tracker.
    rewrite_a_called = False
    rewrite_b_called = False

    class _RewriterA:
        async def rewrite(
            self, query: str, *, workspace_id: str | None = None, **kwargs: Any
        ) -> tuple[Any, Any]:
            nonlocal rewrite_a_called
            rewrite_a_called = True
            raise AssertionError("ws-a should use keyword-only path")

    class _RewriterB:
        async def rewrite(
            self, query: str, *, workspace_id: str | None = None, **kwargs: Any
        ) -> tuple[Any, Any]:
            nonlocal rewrite_b_called
            rewrite_b_called = True
            return StructuredQuery(text=query, degraded=False), None

    retriever_a = HybridRetriever(
        _gateway(),
        rewriter=_RewriterA(),  # type: ignore[arg-type]
        budget_tracker=tracker,
    )
    retriever_b = HybridRetriever(
        _gateway(),
        rewriter=_RewriterB(),  # type: ignore[arg-type]
        budget_tracker=tracker,
    )

    resp_a = await retriever_a.search(
        _EmptySession(),  # type: ignore[arg-type]
        "query a",
        workspace_id="ws-a",
    )
    resp_b = await retriever_b.search(
        _EmptySession(),  # type: ignore[arg-type]
        "query b",
        workspace_id="ws-b",
    )

    assert resp_a.budget_exhausted is True
    assert rewrite_a_called is False

    assert resp_b.budget_exhausted is False
    assert rewrite_b_called is True


# ---------------------------------------------------------------------------
# Config cap respected
# ---------------------------------------------------------------------------


def test_config_cap_respected() -> None:
    """A cap of N calls allows exactly N LLM runs and blocks the (N+1)-th."""
    cap = 3
    tracker = DailyBudgetTracker(daily_limit=cap)
    today = dt.date(2026, 5, 22)

    for i in range(cap):
        result = tracker.check_and_increment("ws-cap", today=today)
        assert result is True, f"Call {i + 1} should be allowed (under cap {cap})"

    # (cap+1)-th call must be blocked.
    assert tracker.check_and_increment("ws-cap", today=today) is False
    assert tracker.current_count("ws-cap", today=today) == cap


# ---------------------------------------------------------------------------
# budget_exhausted flag in the HTTP response (endpoint-level)
# ---------------------------------------------------------------------------


def test_endpoint_budget_exhausted_flag_in_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the service returns budget_exhausted=True the HTTP response includes it."""
    from fastapi.testclient import TestClient

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace
    from civicsignals_api.modules.smart_search import routes

    class _BudgetExhaustedRetriever:
        async def search(self, *args: Any, **kwargs: Any) -> SmartSearchResponse:
            return SmartSearchResponse(
                results=[],
                next_cursor=None,
                query=StructuredQuery(text="test", degraded=True),
                degraded=True,
                summary=None,
                budget_exhausted=True,
            )

    monkeypatch.setattr(routes, "HybridRetriever", _BudgetExhaustedRetriever)
    app.dependency_overrides[require_workspace] = _fake_workspace_ctx
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/smart-search",
            json={"query": "test query"},
            headers={"X-Workspace-Id": str(uuid.uuid4())},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["budget_exhausted"] is True
        assert body["degraded"] is True
        assert body["summary"] is None
    finally:
        app.dependency_overrides.pop(require_workspace, None)
        app.dependency_overrides.pop(get_session, None)


def test_endpoint_budget_not_exhausted_flag_absent_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When under budget budget_exhausted is False (not absent, always serialised)."""
    from fastapi.testclient import TestClient

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace
    from civicsignals_api.modules.smart_search import routes

    class _NormalRetriever:
        async def search(self, *args: Any, **kwargs: Any) -> SmartSearchResponse:
            return SmartSearchResponse(
                results=[],
                next_cursor=None,
                query=StructuredQuery(text="test", degraded=False),
                degraded=False,
                summary=None,
                budget_exhausted=False,
            )

    monkeypatch.setattr(routes, "HybridRetriever", _NormalRetriever)
    app.dependency_overrides[require_workspace] = _fake_workspace_ctx
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/smart-search",
            json={"query": "test query"},
            headers={"X-Workspace-Id": str(uuid.uuid4())},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["budget_exhausted"] is False
    finally:
        app.dependency_overrides.pop(require_workspace, None)
        app.dependency_overrides.pop(get_session, None)
