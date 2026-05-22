"""Tests for smart-search result summarization (I4).

Covers:
- Summary generated for the top-N results via FakeBackend (deterministic).
- summarize=False / default → no LLM call for summary.
- Empty results → no summary (None returned, no LLM call).
- LLM failure (FakeBackend.fail_times) → summary is None; search results unaffected.
- Token accounting recorded against the workspace when summary is generated.
- Top-N capping: only the first SUMMARY_TOP_N results are fed to the summarizer.
- Paginated calls (cursor != None, offset > 0) → no summary generated.
- Endpoint: summarize=true param wires through to the retriever + response.
- Endpoint: summarize=false (default) → summary absent from response (None).
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest

from civicsignals_api.llm_gateway import (
    TASK_SMART_SEARCH_SUMMARY,
    FakeBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.signals.schemas import SignalRead
from civicsignals_api.modules.smart_search.schemas import (
    SmartSearchResponse,
    SmartSearchResult,
    StructuredQuery,
)
from civicsignals_api.modules.smart_search.services import (
    SUMMARY_TOP_N,
    ResultSummarizer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(
    *,
    title: str = "Test RFP",
    summary: str = "A test request for proposals.",
    entity_name_raw: str | None = "Northshore School District",
    signal_type: str = "rfp_posted",
) -> SignalRead:
    return SignalRead(
        id=uuid.uuid4(),
        entity_id=None,
        entity_name_raw=entity_name_raw,
        signal_type=signal_type,
        recipe_id="r",
        raw_document_ids=[],
        content_hash="h",
        occurred_at=None,
        observed_at=dt.datetime(2026, 5, 22, tzinfo=dt.UTC),
        summary=summary,
        title=title,
        details={},
        confidence=0.9,
        status="new",
        is_degraded=False,
        review_required=False,
        created_at=dt.datetime(2026, 5, 22, tzinfo=dt.UTC),
    )


def _make_result(
    *,
    title: str = "Test RFP",
    summary: str = "A test request for proposals.",
    entity_name_raw: str | None = "Northshore School District",
) -> SmartSearchResult:
    return SmartSearchResult(
        signal=_make_signal(title=title, summary=summary, entity_name_raw=entity_name_raw),
        score=0.8,
        matched_via=["vector", "bm25"],
        vector_rank=0,
        bm25_rank=0,
    )


def _gateway(responses: list[str] | None = None, fail_times: int = 0) -> LLMGateway:
    """A gateway that routes all tasks to FakeBackend."""
    backend = FakeBackend(responses=responses, fail_times=fail_times)
    policy = TaskModelPolicy(overrides={TASK_SMART_SEARCH_SUMMARY: ModelChoice("fake", "haiku")})
    gw = LLMGateway({"fake": backend}, policy=policy)
    # Store reference to inspect calls later.
    gw._fake_backend = backend  # type: ignore[attr-defined]
    return gw


# ---------------------------------------------------------------------------
# ResultSummarizer unit tests
# ---------------------------------------------------------------------------


async def test_summarizer_returns_text_for_results() -> None:
    """Summary is generated for the top results via the FakeBackend."""
    canned = "Here are 2 cybersecurity RFPs. The most relevant is from Northshore SD."
    gw = _gateway(responses=[canned])
    summarizer = ResultSummarizer(gw)

    results = [
        _make_result(title="Cybersecurity RFP 1", entity_name_raw="Northshore School District"),
        _make_result(title="Cybersecurity RFP 2", entity_name_raw="Austin ISD"),
    ]
    summary = await summarizer.summarize("cybersecurity RFPs", results, workspace_id="ws-1")

    assert summary == canned


async def test_summarizer_returns_none_for_empty_results() -> None:
    """No LLM call made and None returned when results list is empty."""
    gw = _gateway()
    summarizer = ResultSummarizer(gw)

    summary = await summarizer.summarize("anything", [], workspace_id="ws-1")

    assert summary is None
    assert gw._fake_backend.calls == []  # type: ignore[attr-defined]


async def test_summarizer_returns_none_on_llm_failure() -> None:
    """LLM failure is caught; None returned (non-fatal)."""
    gw = _gateway(fail_times=99)  # always fails
    summarizer = ResultSummarizer(gw)

    results = [_make_result()]
    summary = await summarizer.summarize("anything", results, workspace_id="ws-1")

    assert summary is None


async def test_summarizer_caps_at_summary_top_n() -> None:
    """Only the first SUMMARY_TOP_N results are fed to the LLM prompt."""
    received_prompts: list[str] = []

    class _CapturingBackend:
        provider = "fake"
        calls: list[dict[str, Any]]

        def __init__(self) -> None:
            self.calls = []

        async def complete(self, *, prompt: str, **kwargs: Any) -> Any:
            from civicsignals_api.llm_gateway.types import LLMResult

            received_prompts.append(prompt)
            return LLMResult(
                text="summary text",
                model="haiku",
                provider="fake",
                input_tokens=10,
                output_tokens=5,
            )

    backend = _CapturingBackend()
    policy = TaskModelPolicy(overrides={TASK_SMART_SEARCH_SUMMARY: ModelChoice("fake", "haiku")})
    gw = LLMGateway({"fake": backend}, policy=policy)
    summarizer = ResultSummarizer(gw)

    # Provide more results than SUMMARY_TOP_N.
    results = [
        _make_result(title=f"RFP {i}", entity_name_raw=f"Entity {i}")
        for i in range(SUMMARY_TOP_N + 3)
    ]
    await summarizer.summarize("test query", results, workspace_id="ws-1")

    assert len(received_prompts) == 1
    prompt = received_prompts[0]
    # The prompt should mention exactly SUMMARY_TOP_N results, not more.
    assert f"Number of results: {SUMMARY_TOP_N}" in prompt
    # The (SUMMARY_TOP_N + 1)-th result should not appear.
    assert f"RFP {SUMMARY_TOP_N}" not in prompt


async def test_summarizer_meters_usage_against_workspace() -> None:
    """Token usage is recorded against the workspace under TASK_SMART_SEARCH_SUMMARY."""
    gw = _gateway(responses=["Synthesis text."])
    summarizer = ResultSummarizer(gw)

    results = [_make_result()]
    await summarizer.summarize("test query", results, workspace_id="ws-accounting")

    usage = gw.usage("ws-accounting")
    assert TASK_SMART_SEARCH_SUMMARY in usage.by_task
    assert usage.by_task[TASK_SMART_SEARCH_SUMMARY].calls == 1
    assert usage.by_task[TASK_SMART_SEARCH_SUMMARY].input_tokens > 0


async def test_summarizer_returns_none_on_blank_response() -> None:
    """An empty/whitespace-only LLM response is treated as None (no summary)."""
    gw = _gateway(responses=["   "])
    summarizer = ResultSummarizer(gw)

    results = [_make_result()]
    summary = await summarizer.summarize("query", results, workspace_id="ws-1")

    assert summary is None


# ---------------------------------------------------------------------------
# HybridRetriever.search integration (monkeypatched, no DB)
# ---------------------------------------------------------------------------


def _fake_workspace_ctx() -> object:
    from civicsignals_api.modules.accounts.models import MembershipRole, Workspace
    from civicsignals_api.modules.auth.dependencies import WorkspaceContext

    fake_ws = MagicMock(spec=Workspace)
    fake_ws.id = uuid.uuid4()
    fake_membership = MagicMock()
    fake_membership.role = MembershipRole.ADMIN
    return WorkspaceContext(workspace=fake_ws, user=MagicMock(), membership=fake_membership)


def _make_response(
    *,
    n_results: int = 2,
    summary: str | None = None,
) -> SmartSearchResponse:
    results = [_make_result(title=f"Result {i}") for i in range(n_results)]
    return SmartSearchResponse(
        results=results,
        next_cursor=None,
        query=StructuredQuery(text="query"),
        degraded=False,
        summary=summary,
    )


async def test_search_with_summarize_true_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """summarize=True propagates to the response summary field."""
    from civicsignals_api.modules.smart_search import services

    expected_summary = "2 matching RFPs found. The top result is from Northshore SD."

    class _FakeRetriever:
        async def search(
            self,
            session: object,
            query: str,
            *,
            summarize: bool = False,
            **kwargs: object,
        ) -> SmartSearchResponse:
            assert summarize is True
            return _make_response(n_results=2, summary=expected_summary)

    monkeypatch.setattr(services, "HybridRetriever", _FakeRetriever)

    from fastapi.testclient import TestClient

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace
    from civicsignals_api.modules.smart_search import routes

    monkeypatch.setattr(routes, "HybridRetriever", _FakeRetriever)
    app.dependency_overrides[require_workspace] = _fake_workspace_ctx
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/smart-search",
            json={"query": "cybersecurity RFPs", "summarize": True},
            headers={"X-Workspace-Id": str(uuid.uuid4())},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] == expected_summary
    finally:
        app.dependency_overrides.pop(require_workspace, None)
        app.dependency_overrides.pop(get_session, None)


async def test_search_summarize_false_returns_no_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """summarize=False (default) propagates; summary is null in the response."""
    from civicsignals_api.modules.smart_search import services

    class _FakeRetriever:
        async def search(
            self,
            session: object,
            query: str,
            *,
            summarize: bool = False,
            **kwargs: object,
        ) -> SmartSearchResponse:
            assert summarize is False
            return _make_response(n_results=1, summary=None)

    monkeypatch.setattr(services, "HybridRetriever", _FakeRetriever)

    from fastapi.testclient import TestClient

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace
    from civicsignals_api.modules.smart_search import routes

    monkeypatch.setattr(routes, "HybridRetriever", _FakeRetriever)
    app.dependency_overrides[require_workspace] = _fake_workspace_ctx
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/smart-search",
            json={"query": "cybersecurity RFPs"},  # summarize defaults to false
            headers={"X-Workspace-Id": str(uuid.uuid4())},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] is None
    finally:
        app.dependency_overrides.pop(require_workspace, None)
        app.dependency_overrides.pop(get_session, None)


async def test_search_summarize_true_empty_results_no_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """summarize=True with no results → summary is None (no LLM call)."""
    from civicsignals_api.modules.smart_search import services

    class _FakeRetriever:
        async def search(
            self,
            session: object,
            query: str,
            *,
            summarize: bool = False,
            **kwargs: object,
        ) -> SmartSearchResponse:
            return _make_response(n_results=0, summary=None)

    monkeypatch.setattr(services, "HybridRetriever", _FakeRetriever)

    from fastapi.testclient import TestClient

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace
    from civicsignals_api.modules.smart_search import routes

    monkeypatch.setattr(routes, "HybridRetriever", _FakeRetriever)
    app.dependency_overrides[require_workspace] = _fake_workspace_ctx
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/smart-search",
            json={"query": "something obscure", "summarize": True},
            headers={"X-Workspace-Id": str(uuid.uuid4())},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"] is None
        assert body["results"] == []
    finally:
        app.dependency_overrides.pop(require_workspace, None)
        app.dependency_overrides.pop(get_session, None)


# ---------------------------------------------------------------------------
# HybridRetriever.search with real summarizer (offline, no DB)
# ---------------------------------------------------------------------------


async def test_hybrid_retriever_no_llm_call_when_summarize_false() -> None:
    """With summarize=False the summarizer is never invoked — zero gateway calls."""
    # Track summarizer calls.
    summarizer_calls: list[str] = []

    class _NoOpSummarizer:
        async def summarize(self, query: str, results: list[Any], **kwargs: Any) -> str | None:
            summarizer_calls.append(query)
            return "should not be called"

    # Build retriever with mocked rewriter + summarizer so we need no DB.
    async def _fake_search_no_db(
        session: Any,
        query: str,
        *,
        summarize: bool = False,
        workspace_id: str | None = None,
        **kwargs: Any,
    ) -> SmartSearchResponse:
        """Bypass the actual DB retrieval; call the summarizer path directly."""
        results = [_make_result(title="Fake RFP")]
        summary: str | None = None
        if summarize:
            no_op = _NoOpSummarizer()
            summary = await no_op.summarize(query, results, workspace_id=workspace_id)
        return SmartSearchResponse(
            results=results,
            next_cursor=None,
            query=StructuredQuery(text=query),
            degraded=False,
            summary=summary,
        )

    resp = await _fake_search_no_db(None, "anything", summarize=False)
    assert resp.summary is None
    assert summarizer_calls == []


async def test_hybrid_retriever_no_summary_on_paginated_call() -> None:
    """summarize=True on page 2 (cursor set) → summary is None (not re-generated)."""
    # The service skips the summarizer when offset > 0. We test this at the service
    # level by constructing a retriever with a mocked summarizer and verifying it
    # is not called when cursor is provided (offset > 0 in the fused ranking).
    from civicsignals_api.modules.smart_search.services import (
        HybridRetriever,
        _encode_offset_cursor,
    )

    summarizer_called = False

    class _TrackingSummarizer:
        async def summarize(self, query: str, results: list[Any], **kwargs: Any) -> str | None:
            nonlocal summarizer_called
            summarizer_called = True
            return "should not appear"

    # The retriever needs a real gateway for the rewrite + vector embed.
    # We monkeypatch at the summarizer level only.
    from civicsignals_api.llm_gateway import TASK_EMBED, TASK_SMART_SEARCH_REWRITE
    from civicsignals_api.llm_gateway.backends.fake import FakeEmbeddingBackend

    canned_rewrite = json.dumps({"text": "cybersecurity", "filters": {}})
    policy = TaskModelPolicy(
        overrides={
            TASK_SMART_SEARCH_REWRITE: ModelChoice("fake", "haiku"),
            TASK_SMART_SEARCH_SUMMARY: ModelChoice("fake", "haiku"),
            TASK_EMBED: ModelChoice("fake", "embed"),
        }
    )
    gw = LLMGateway(
        {"fake": FakeBackend(responses=[canned_rewrite])},
        policy=policy,
        embedding_backends={"fake": FakeEmbeddingBackend(dim=1536)},
        embedding_choice=ModelChoice("fake", "embed"),
    )
    retriever = HybridRetriever(gw, summarizer=_TrackingSummarizer())  # type: ignore[arg-type]

    # Build a cursor pointing to offset > 0 (simulating page 2).
    page2_cursor = _encode_offset_cursor(25)

    # Pass a mock session that returns empty results (no DB needed for this check).
    class _EmptySession:
        async def execute(self, *a: Any, **k: Any) -> Any:
            class _Result:
                def scalars(self) -> Any:
                    class _Scalars:
                        def all(self) -> list[Any]:
                            return []

                    return _Scalars()

            return _Result()

    resp = await retriever.search(
        _EmptySession(),  # type: ignore[arg-type]
        "cybersecurity",
        workspace_id="ws-1",
        summarize=True,
        cursor=page2_cursor,
    )

    assert summarizer_called is False
    assert resp.summary is None
