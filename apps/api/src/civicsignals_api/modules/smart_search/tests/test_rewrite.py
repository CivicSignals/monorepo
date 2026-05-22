"""Tests for the smart-search NL -> structured query rewrite (TODO I2).

All LLM access goes through the gateway's FakeBackend (canned JSON, no network).
Covers: NL -> structured for representative queries, invalid-output retry+fallback,
strict schema validation/repair, workspace token accounting, and the thin endpoint.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from civicsignals_api.llm_gateway import (
    TASK_SMART_SEARCH_REWRITE,
    FakeBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
    TransientLLMError,
)
from civicsignals_api.main import app
from civicsignals_api.modules.smart_search import routes
from civicsignals_api.modules.smart_search.schemas import SearchFilters, StructuredQuery
from civicsignals_api.modules.smart_search.services import QueryRewriter


def _gateway(backend: FakeBackend, **kwargs: object) -> LLMGateway:
    policy = TaskModelPolicy(overrides={TASK_SMART_SEARCH_REWRITE: ModelChoice("fake", "haiku")})
    return LLMGateway({"fake": backend}, policy=policy, **kwargs)  # type: ignore[arg-type]


# --- NL -> structured (canned good output) -------------------------------


async def test_rewrite_full_filters_and_residual_text() -> None:
    canned = json.dumps(
        {
            "text": "math curriculum",
            "keywords": ["curriculum", "math"],
            "entities": ["Northshore School District"],
            "filters": {
                "signal_type": ["rfp_posted"],
                "entity_kind": ["k12_district"],
                "state": ["wa"],
                "min_score": 0.6,
                "published_at_gte": "2026-05-01",
                "published_at_lt": "2026-06-01",
            },
        }
    )
    rewriter = QueryRewriter(_gateway(FakeBackend(responses=[canned])))
    query, result = await rewriter.rewrite("open WA k-12 math curriculum RFPs since May")

    assert query.degraded is False
    assert query.text == "math curriculum"
    assert query.keywords == ["curriculum", "math"]
    assert query.entities == ["Northshore School District"]
    assert query.filters.signal_type == ["rfp_posted"]
    assert query.filters.entity_kind == ["k12_district"]
    assert query.filters.state == ["WA"]  # normalized upper-case
    assert query.filters.min_score == 0.6
    assert query.filters.published_at_gte == dt.date(2026, 5, 1)
    assert query.filters.published_at_lt == dt.date(2026, 6, 1)
    assert result is not None
    assert (result.provider, result.model) == ("fake", "haiku")


async def test_rewrite_keyword_only_query_has_empty_filters() -> None:
    canned = json.dumps({"text": "broadband expansion", "filters": {}})
    rewriter = QueryRewriter(_gateway(FakeBackend(responses=[canned])))
    query, _ = await rewriter.rewrite("broadband expansion")

    assert query.text == "broadband expansion"
    assert query.filters.is_empty()
    assert query.degraded is False


async def test_rewrite_tolerates_markdown_fenced_json() -> None:
    canned = (
        "Here you go:\n```json\n"
        + json.dumps({"text": "grants", "filters": {"signal_type": ["grant_awarded"]}})
        + "\n```"
    )
    rewriter = QueryRewriter(_gateway(FakeBackend(responses=[canned])))
    query, _ = await rewriter.rewrite("recent grant awards")

    assert query.filters.signal_type == ["grant_awarded"]
    assert query.text == "grants"
    assert query.degraded is False


async def test_rewrite_all_filters_keeps_original_as_text() -> None:
    # Model returns no residual text; we backfill the original so full-text search
    # still has something to work with.
    canned = json.dumps({"filters": {"status": ["new"]}})
    rewriter = QueryRewriter(_gateway(FakeBackend(responses=[canned])))
    query, _ = await rewriter.rewrite("show me new signals")

    assert query.filters.status == ["new"]
    assert query.text == "show me new signals"


# --- repair: out-of-enum / wrong-type values dropped, not fatal ----------


async def test_rewrite_drops_hallucinated_enum_values() -> None:
    canned = json.dumps(
        {
            "text": "stuff",
            "filters": {
                "signal_type": ["rfp_posted", "made_up_type"],
                "entity_kind": ["not_a_kind"],
                "status": "new",  # scalar repaired to a list
            },
        }
    )
    rewriter = QueryRewriter(_gateway(FakeBackend(responses=[canned])))
    query, _ = await rewriter.rewrite("stuff")

    assert query.filters.signal_type == ["rfp_posted"]  # bad value dropped
    assert query.filters.entity_kind == []  # all invalid -> dropped
    assert query.filters.status == ["new"]  # scalar coerced to list
    assert query.degraded is False


# --- invalid output: retry once, then graceful fallback ------------------


async def test_rewrite_non_json_then_valid_recovers_via_retry() -> None:
    good = json.dumps({"text": "rfps", "filters": {"signal_type": ["rfp_posted"]}})
    backend = FakeBackend(responses=["not json at all", good])
    rewriter = QueryRewriter(_gateway(backend))
    query, _ = await rewriter.rewrite("rfps")

    assert len(backend.calls) == 2  # parse failed once, retried
    assert query.filters.signal_type == ["rfp_posted"]
    assert query.degraded is False


async def test_rewrite_persistent_garbage_falls_back_to_text() -> None:
    backend = FakeBackend(responses=["garbage", "still garbage"])
    rewriter = QueryRewriter(_gateway(backend))
    query, result = await rewriter.rewrite("housing bond measures in Texas")

    assert query.degraded is True
    assert query.text == "housing bond measures in Texas"
    assert query.filters.is_empty()
    assert result is not None  # last (still-invalid) result is returned for usage


async def test_rewrite_invalid_schema_date_range_falls_back() -> None:
    # Backwards date range fails the model validator -> repair retry -> fallback.
    bad = json.dumps(
        {
            "text": "x",
            "filters": {"published_at_gte": "2026-06-01", "published_at_lt": "2026-05-01"},
        }
    )
    backend = FakeBackend(responses=[bad, bad])
    rewriter = QueryRewriter(_gateway(backend))
    query, _ = await rewriter.rewrite("x signals")

    assert query.degraded is True
    assert query.text == "x signals"


async def test_rewrite_llm_error_falls_back_without_result() -> None:
    # The gateway exhausts its own retries and reraises; we degrade gracefully.
    backend = FakeBackend(fail_times=5)
    rewriter = QueryRewriter(_gateway(backend, max_attempts=2))
    query, result = await rewriter.rewrite("anything")

    assert query.degraded is True
    assert query.text == "anything"
    assert result is None


async def test_rewrite_empty_query_short_circuits() -> None:
    backend = FakeBackend(responses=["unused"])
    rewriter = QueryRewriter(_gateway(backend))
    query, result = await rewriter.rewrite("   ")

    assert query.degraded is True
    assert query.text == ""
    assert result is None
    assert backend.calls == []  # never reached the model


# --- token accounting / provenance ---------------------------------------


async def test_rewrite_records_workspace_usage() -> None:
    canned = json.dumps({"text": "rfps", "filters": {}})
    gw = _gateway(FakeBackend(responses=[canned]))
    rewriter = QueryRewriter(gw)
    await rewriter.rewrite("open rfps", workspace_id="ws-1")

    usage = gw.usage("ws-1")
    assert usage.calls == 1
    assert usage.by_task[TASK_SMART_SEARCH_REWRITE].calls == 1
    assert usage.input_tokens > 0


# --- workspace context steers the prompt (not the schema) ----------------


async def test_workspace_context_added_to_prompt() -> None:
    canned = json.dumps({"text": "rfps", "filters": {}})
    backend = FakeBackend(responses=[canned])
    rewriter = QueryRewriter(_gateway(backend))
    await rewriter.rewrite(
        "rfps",
        available_signal_types=["rfp_posted", "bogus_type"],
        available_states=["wa", "or"],
    )

    prompt = str(backend.calls[0]["prompt"])
    assert "rfp_posted" in prompt
    assert "bogus_type" not in prompt  # filtered to known enum values
    assert "OR, WA" in prompt or "WA, OR" in prompt  # sorted, upper-cased


# --- pure schema unit checks ---------------------------------------------


def test_search_filters_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        SearchFilters.model_validate({"made_up": ["x"]})


def test_search_filters_rejects_backwards_date_range() -> None:
    with pytest.raises(ValueError, match="published_at_lt must be after"):
        SearchFilters(published_at_gte=dt.date(2026, 6, 1), published_at_lt=dt.date(2026, 5, 1))


def test_search_filters_dedupes_and_uppercases_state() -> None:
    f = SearchFilters(state=["wa", "WA", " or "])
    assert f.state == ["WA", "OR"]


def test_structured_query_is_empty_helper() -> None:
    assert SearchFilters().is_empty()
    assert not SearchFilters(min_score=0.0).is_empty()
    assert StructuredQuery(text="hi").filters.is_empty()


# --- the thin endpoint ----------------------------------------------------


def test_rewrite_endpoint_returns_structured_query(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = json.dumps(
        {"text": "math", "filters": {"signal_type": ["rfp_posted"], "state": ["WA"]}}
    )
    gw = _gateway(FakeBackend(responses=[canned]))

    # Route constructs QueryRewriter() with the process gateway; swap it for one
    # bound to the FakeBackend so the endpoint test is deterministic + offline.
    def _fake_rewriter() -> QueryRewriter:
        return QueryRewriter(gw)

    monkeypatch.setattr(routes, "QueryRewriter", _fake_rewriter)

    client = TestClient(app)
    response = client.post(
        "/api/v1/smart-search/rewrite",
        json={"query": "WA k-12 math RFPs"},
        headers={"X-Workspace-Id": "ws-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"]["filters"]["signal_type"] == ["rfp_posted"]
    assert body["query"]["filters"]["state"] == ["WA"]
    assert body["query"]["degraded"] is False
    assert body["usage"]["provider"] == "fake"
    assert body["usage"]["input_tokens"] > 0
    # Usage was metered against the workspace from the header.
    assert gw.usage("ws-1").calls == 1


def test_rewrite_endpoint_rejects_empty_query() -> None:
    client = TestClient(app)
    response = client.post("/api/v1/smart-search/rewrite", json={"query": ""})
    assert response.status_code == 422


def test_transient_error_type_importable() -> None:
    # Guard: the service relies on LLMError (TransientLLMError subclass) bubbling.
    assert issubclass(TransientLLMError, Exception)
