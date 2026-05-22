"""Tests for smart-search hybrid retrieval (TODO I3, doc 14 §6.2).

Two layers:

- **Pure / deterministic** (always run): the RRF fusion ranking, cursor encoding,
  filter merge, and weight validation — the ranking logic is independent of the DB
  so its order is asserted exactly.
- **DB-backed end-to-end** (skips without a Postgres DSN): seed ``signals_signal``
  rows, embed them via the deterministic FakeEmbeddingBackend, and run
  ``HybridRetriever.search`` to assert vector hit, BM25 hit, filter intersection,
  fusion order, empty/no-results, and the workspace-context accounting.

All LLM/embedding access goes through the gateway's Fake backends (no network).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from civicsignals_api.db import Base
from civicsignals_api.llm_gateway import (
    TASK_EMBED,
    TASK_SMART_SEARCH_REWRITE,
    FakeBackend,
    FakeEmbeddingBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.signals import services as signals_services
from civicsignals_api.modules.signals.embedding import embed_signals
from civicsignals_api.modules.signals.models import Signal
from civicsignals_api.modules.signals.services import CandidateInput
from civicsignals_api.modules.smart_search.schemas import FusionWeights, SearchFilters
from civicsignals_api.modules.smart_search.services import (
    HybridRetriever,
    _decode_offset_cursor,
    _encode_offset_cursor,
    _merge_filters,
    fuse_rankings,
)

# Small embedding dim for the fakes; the column is VECTOR(1536) so the DB-backed
# tests use the real dim, while the pure tests do not touch the DB at all.
EMBED_DIM = 1536


# =========================================================================
# Pure: RRF fusion ranking
# =========================================================================


def _ids(n: int) -> list[uuid.UUID]:
    # Deterministic, ordered ids so tiebreaks are reproducible across runs.
    return [uuid.UUID(int=i) for i in range(1, n + 1)]


def test_fuse_prefers_signals_high_in_both_lists() -> None:
    a, b, c = _ids(3)
    # `a` is rank 0 in both lists -> highest fused score; `c` only in BM25 last.
    hits = fuse_rankings(
        vector_ids=[a, b],
        bm25_ids=[a, b, c],
        filter_ids=None,
        weights=FusionWeights(),
    )
    order = [h.signal_id for h in hits]
    assert order[0] == a  # top of both lists
    assert set(order) == {a, b, c}
    # `a` strictly beats `b` (both in both lists, `a` higher), `b` beats `c`.
    by_id = {h.signal_id: h for h in hits}
    assert by_id[a].score > by_id[b].score > by_id[c].score
    assert by_id[a].matched_via == ("vector", "bm25")
    assert by_id[c].matched_via == ("bm25",)
    assert by_id[a].vector_rank == 0 and by_id[a].bm25_rank == 0
    assert by_id[c].vector_rank is None and by_id[c].bm25_rank == 2


def test_fuse_intersects_with_filter_ids() -> None:
    a, b, c = _ids(3)
    # `b` is surfaced by both retrievers but excluded by the structured filter.
    hits = fuse_rankings(
        vector_ids=[a, b],
        bm25_ids=[b, c],
        filter_ids={a, c},
        weights=FusionWeights(),
    )
    ids = {h.signal_id for h in hits}
    assert ids == {a, c}  # b filtered out
    assert all("filter" in h.matched_via for h in hits)


def test_fuse_filter_only_signal_still_ranks() -> None:
    # A filter-only query: a signal in the filter set but not surfaced by vector/BM25
    # still returns (score 0 from fusion) so a pure structured query yields results.
    a, b = _ids(2)
    hits = fuse_rankings(vector_ids=[], bm25_ids=[], filter_ids={a, b}, weights=FusionWeights())
    assert {h.signal_id for h in hits} == {a, b}
    assert all(h.score == 0.0 for h in hits)
    assert all(h.matched_via == ("filter",) for h in hits)


def test_fuse_weights_shift_ranking() -> None:
    a, b = _ids(2)
    # `a` top of vector, `b` top of BM25. With vector-heavy weights, `a` wins;
    # with BM25-heavy weights, `b` wins. Same inputs, opposite order.
    vector_heavy = fuse_rankings(
        vector_ids=[a, b],
        bm25_ids=[b, a],
        filter_ids=None,
        weights=FusionWeights(vector=10.0, bm25=1.0),
    )
    bm25_heavy = fuse_rankings(
        vector_ids=[a, b],
        bm25_ids=[b, a],
        filter_ids=None,
        weights=FusionWeights(vector=1.0, bm25=10.0),
    )
    assert vector_heavy[0].signal_id == a
    assert bm25_heavy[0].signal_id == b


def test_fuse_zero_vector_weight_drops_vector_contribution() -> None:
    a, b = _ids(2)
    hits = fuse_rankings(
        vector_ids=[a],  # only vector surfaces `a`
        bm25_ids=[b],  # only bm25 surfaces `b`
        filter_ids=None,
        weights=FusionWeights(vector=0.0, bm25=1.0),
    )
    by_id = {h.signal_id: h for h in hits}
    # `a`'s vector contribution is zeroed -> score 0; `b` scores from BM25.
    assert by_id[a].score == 0.0
    assert by_id[b].score > 0.0
    assert "vector" not in by_id[a].matched_via


def test_fuse_empty_returns_empty() -> None:
    assert fuse_rankings(vector_ids=[], bm25_ids=[], filter_ids=None, weights=FusionWeights()) == []


def test_fuse_is_deterministic_tiebreak() -> None:
    # Two signals with identical fused scores must order stably by id across calls.
    a, b = _ids(2)
    first = fuse_rankings(
        vector_ids=[a, b], bm25_ids=[b, a], filter_ids=None, weights=FusionWeights()
    )
    second = fuse_rankings(
        vector_ids=[a, b], bm25_ids=[b, a], filter_ids=None, weights=FusionWeights()
    )
    assert [h.signal_id for h in first] == [h.signal_id for h in second]


# =========================================================================
# Pure: weights validation, cursor, filter merge
# =========================================================================


def test_fusion_weights_rejects_all_zero() -> None:
    with pytest.raises(ValueError, match="at least one"):
        FusionWeights(vector=0.0, bm25=0.0)


def test_fusion_weights_rejects_negative() -> None:
    with pytest.raises(ValueError):
        FusionWeights(vector=-1.0)


def test_cursor_round_trips() -> None:
    for offset in (0, 1, 25, 1000):
        assert _decode_offset_cursor(_encode_offset_cursor(offset)) == offset


def test_cursor_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        _decode_offset_cursor("not-a-cursor!!")


def test_cursor_rejects_negative() -> None:
    # A token whose decoded int is negative is rejected (would page backwards).
    negative_token = base64.urlsafe_b64encode(b"-3").decode("ascii")
    with pytest.raises(ValueError):
        _decode_offset_cursor(negative_token)


def test_merge_filters_unions_lists_and_overrides_scalars() -> None:
    base = SearchFilters(signal_type=["rfp_posted"], state=["WA"], min_score=0.2)
    extra = SearchFilters(signal_type=["grant_awarded"], state=["OR"], min_score=0.5)
    merged = _merge_filters(base, extra)
    assert set(merged.signal_type) == {"rfp_posted", "grant_awarded"}
    assert set(merged.state) == {"WA", "OR"}
    assert merged.min_score == 0.5  # explicit extra wins


def test_merge_filters_none_extra_returns_base() -> None:
    base = SearchFilters(signal_type=["rfp_posted"])
    assert _merge_filters(base, None) is base


# =========================================================================
# Endpoint wiring (DB-free): the route delegates to HybridRetriever behind
# require_workspace and maps an invalid cursor to RFC 7807. The retriever is
# monkeypatched so the route test is deterministic + offline.
# =========================================================================


def _fake_workspace_ctx() -> object:
    from unittest.mock import MagicMock

    from civicsignals_api.modules.accounts.models import MembershipRole, Workspace
    from civicsignals_api.modules.auth.dependencies import WorkspaceContext

    fake_ws = MagicMock(spec=Workspace)
    fake_ws.id = uuid.uuid4()
    fake_membership = MagicMock()
    fake_membership.role = MembershipRole.ADMIN
    return WorkspaceContext(workspace=fake_ws, user=MagicMock(), membership=fake_membership)


def test_smart_search_endpoint_returns_ranked_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace
    from civicsignals_api.modules.signals.schemas import SignalRead
    from civicsignals_api.modules.smart_search import routes
    from civicsignals_api.modules.smart_search.schemas import (
        SmartSearchResponse,
        SmartSearchResult,
        StructuredQuery,
    )

    captured: dict[str, object] = {}

    class _FakeRetriever:
        async def search(
            self, session: object, query: str, **kwargs: object
        ) -> SmartSearchResponse:
            captured["query"] = query
            captured["workspace_id"] = kwargs.get("workspace_id")
            signal = SignalRead(
                id=uuid.uuid4(),
                entity_id=None,
                entity_name_raw=None,
                signal_type="rfp_posted",
                recipe_id="r",
                raw_document_ids=[],
                content_hash="h",
                occurred_at=None,
                observed_at=dt.datetime(2026, 5, 22, tzinfo=dt.UTC),
                summary="s",
                title="t",
                details={},
                confidence=0.9,
                status="new",
                is_degraded=False,
                review_required=False,
                created_at=dt.datetime(2026, 5, 22, tzinfo=dt.UTC),
            )
            return SmartSearchResponse(
                results=[
                    SmartSearchResult(
                        signal=signal,
                        score=0.5,
                        matched_via=["vector", "bm25"],
                        vector_rank=0,
                        bm25_rank=0,
                    )
                ],
                next_cursor="abc",
                query=StructuredQuery(text="cyber", filters={"signal_type": ["rfp_posted"]}),
                degraded=False,
            )

    monkeypatch.setattr(routes, "HybridRetriever", _FakeRetriever)
    app.dependency_overrides[require_workspace] = _fake_workspace_ctx
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/smart-search",
            json={"query": "cyber RFPs", "top_n": 5},
            headers={"X-Workspace-Id": str(uuid.uuid4())},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["results"]) == 1
        assert body["results"][0]["matched_via"] == ["vector", "bm25"]
        assert body["results"][0]["score"] == 0.5
        assert body["next_cursor"] == "abc"
        assert body["query"]["filters"]["signal_type"] == ["rfp_posted"]
        assert captured["query"] == "cyber RFPs"
        # Workspace context was threaded into the retriever (workspace scoping).
        assert captured["workspace_id"] is not None
    finally:
        app.dependency_overrides.pop(require_workspace, None)
        app.dependency_overrides.pop(get_session, None)


def test_smart_search_endpoint_invalid_cursor_is_problem(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace
    from civicsignals_api.modules.smart_search import routes

    class _RaisingRetriever:
        async def search(self, *args: object, **kwargs: object) -> object:
            raise ValueError("invalid cursor")

    monkeypatch.setattr(routes, "HybridRetriever", _RaisingRetriever)
    app.dependency_overrides[require_workspace] = _fake_workspace_ctx
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/smart-search",
            json={"query": "cyber", "cursor": "garbage"},
            headers={"X-Workspace-Id": str(uuid.uuid4())},
        )
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")
    finally:
        app.dependency_overrides.pop(require_workspace, None)
        app.dependency_overrides.pop(get_session, None)


def test_smart_search_endpoint_rejects_empty_query() -> None:
    from fastapi.testclient import TestClient

    from civicsignals_api.db import get_session
    from civicsignals_api.main import app
    from civicsignals_api.modules.auth.dependencies import require_workspace

    app.dependency_overrides[require_workspace] = _fake_workspace_ctx
    app.dependency_overrides[get_session] = lambda: None
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/smart-search",
            json={"query": ""},
            headers={"X-Workspace-Id": str(uuid.uuid4())},
        )
        assert resp.status_code == 422
    finally:
        app.dependency_overrides.pop(require_workspace, None)
        app.dependency_overrides.pop(get_session, None)


# =========================================================================
# DB-backed end-to-end
# =========================================================================

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)

_TABLE = "signals_signal"
_ENTITY_TABLES = ("entities_entity", "entities_geo", "entities_kind")


def _drop_cascade(conn: Connection) -> None:
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")
    for tbl in _ENTITY_TABLES:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl} CASCADE")


def _gateway() -> LLMGateway:
    """A gateway with a Fake completion backend (rewrite) + Fake embedding backend."""
    policy = TaskModelPolicy(
        overrides={
            TASK_SMART_SEARCH_REWRITE: ModelChoice("fake", "haiku"),
            TASK_EMBED: ModelChoice("fake", "embed"),
        }
    )
    return LLMGateway(
        {"fake": FakeBackend()},
        policy=policy,
        embedding_backends={"fake": FakeEmbeddingBackend(dim=EMBED_DIM)},
        embedding_choice=ModelChoice("fake", "embed"),
    )


def _retriever_with_rewrite(canned: dict[str, object]) -> tuple[HybridRetriever, LLMGateway]:
    """A retriever whose rewrite returns a canned structured query (offline)."""
    policy = TaskModelPolicy(
        overrides={
            TASK_SMART_SEARCH_REWRITE: ModelChoice("fake", "haiku"),
            TASK_EMBED: ModelChoice("fake", "embed"),
        }
    )
    gw = LLMGateway(
        {"fake": FakeBackend(responses=[json.dumps(canned)])},
        policy=policy,
        embedding_backends={"fake": FakeEmbeddingBackend(dim=EMBED_DIM)},
        embedding_choice=ModelChoice("fake", "embed"),
    )
    return HybridRetriever(gw), gw


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    # Gate only the DB-backed tests (those that depend on this fixture); the pure
    # fusion/cursor/merge/endpoint tests above run regardless of a DSN.
    if _DSN is None:
        pytest.skip("no Postgres DSN configured")
    engine = create_async_engine(_DSN)
    create_tables = [Base.metadata.tables[name] for name in _ENTITY_TABLES] + [
        Base.metadata.tables[_TABLE]
    ]
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(_drop_cascade)
        await conn.run_sync(Base.metadata.create_all, tables=create_tables, checkfirst=True)
        # The FTS GIN index the BM25 leg uses (the I3 migration's expression).
        await conn.execute(
            text(
                "CREATE INDEX signals_fts_gin_idx ON signals_signal USING gin "
                "(to_tsvector('english', coalesce(title, '') || ' ' || coalesce(summary, '')))"
            )
        )
    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_cascade)
        await engine.dispose()


async def _seed_signal(
    session: AsyncSession,
    *,
    title: str,
    summary: str,
    signal_type: str = "news_mention",
    content_hash: str,
    occurred_at: dt.datetime | None = None,
    fields: dict[str, object] | None = None,
) -> Signal:
    candidate = CandidateInput(
        signal_type=signal_type,
        fields=fields or {"title": title, "summary": summary},
        recipe_id="test_recipe",
        raw_document_id=uuid.uuid4(),
        content_hash=content_hash,
        confidence=0.9,
        occurred_at=occurred_at,
    )
    signal = await signals_services.promote_candidate_to_signal(session, candidate)
    return signal


async def _embed_all(session: AsyncSession) -> None:
    ids = list((await session.execute(text("SELECT id FROM signals_signal"))).scalars().all())
    await embed_signals(
        session, [uuid.UUID(str(i)) for i in ids], gateway=_gateway(), workspace_id=None
    )


async def test_end_to_end_vector_and_bm25_hits(session: AsyncSession) -> None:
    # Two signals: one whose title matches the BM25 text, one whose embedding text
    # is closest to the query (the deterministic fake embeds identical text alike).
    bm25_hit = await _seed_signal(
        session,
        title="Cybersecurity RFP for school district network",
        summary="A request for proposals for cybersecurity services.",
        content_hash="bm25",
    )
    other = await _seed_signal(
        session,
        title="Annual budget overview",
        summary="The district published its annual budget summary.",
        content_hash="vec",
    )
    await session.commit()
    await _embed_all(session)
    await session.commit()

    retriever, _ = _retriever_with_rewrite({"text": "cybersecurity RFP", "filters": {}})
    resp = await retriever.search(session, "cybersecurity RFP", workspace_id="ws-1", top_n=10)

    ids = [r.signal.id for r in resp.results]
    assert bm25_hit.id in ids  # BM25 surfaces the cybersecurity RFP
    # The BM25 hit (matched by both vector + BM25 text overlap) ranks first.
    assert resp.results[0].signal.id == bm25_hit.id
    assert "bm25" in resp.results[0].matched_via
    assert resp.degraded is False
    assert other.id in ids  # vector ANN still surfaces the other signal


async def test_end_to_end_filter_intersection(session: AsyncSession) -> None:
    rfp = await _seed_signal(
        session,
        title="RFP cybersecurity platform",
        summary="cybersecurity platform request for proposals",
        signal_type="news_mention",
        content_hash="a",
    )
    grant = await _seed_signal(
        session,
        title="Grant award cybersecurity",
        summary="cybersecurity grant awarded to the district",
        signal_type="grant_awarded",
        content_hash="b",
        fields={
            "title": "Grant award cybersecurity",
            "summary": "cybersecurity grant awarded to the district",
        },
    )
    await session.commit()
    await _embed_all(session)
    await session.commit()

    # Rewrite extracts a signal_type filter; only the grant survives the intersection.
    retriever, _ = _retriever_with_rewrite(
        {"text": "cybersecurity", "filters": {"signal_type": ["grant_awarded"]}}
    )
    resp = await retriever.search(session, "cybersecurity grants", workspace_id="ws-1")
    ids = {r.signal.id for r in resp.results}
    assert ids == {grant.id}
    assert rfp.id not in ids
    assert all("filter" in r.matched_via for r in resp.results)


async def test_end_to_end_no_results(session: AsyncSession) -> None:
    await _seed_signal(session, title="Budget summary", summary="annual budget", content_hash="x")
    await session.commit()
    await _embed_all(session)
    await session.commit()

    # A filter that matches nothing -> empty intersection -> no results.
    retriever, _ = _retriever_with_rewrite(
        {"text": "anything", "filters": {"signal_type": ["rfp_posted"]}}
    )
    resp = await retriever.search(session, "anything", workspace_id="ws-1")
    assert resp.results == []
    assert resp.next_cursor is None


async def test_end_to_end_workspace_context_meters_usage(session: AsyncSession) -> None:
    await _seed_signal(
        session, title="Cybersecurity RFP", summary="cybersecurity rfp", content_hash="ws"
    )
    await session.commit()
    await _embed_all(session)
    await session.commit()

    retriever, gw = _retriever_with_rewrite({"text": "cybersecurity", "filters": {}})
    await retriever.search(session, "cybersecurity", workspace_id="ws-meter")

    usage = gw.usage("ws-meter")
    # The rewrite + the query embed both metered against the workspace.
    assert usage.by_task[TASK_SMART_SEARCH_REWRITE].calls == 1
    assert usage.by_task[TASK_EMBED].calls == 1


async def test_end_to_end_cursor_pagination(session: AsyncSession) -> None:
    for i in range(3):
        await _seed_signal(
            session,
            title=f"Cybersecurity RFP number {i}",
            summary=f"cybersecurity request {i}",
            content_hash=f"page-{i}",
        )
    await session.commit()
    await _embed_all(session)
    await session.commit()

    retriever, _ = _retriever_with_rewrite({"text": "cybersecurity", "filters": {}})
    page1 = await retriever.search(session, "cybersecurity", workspace_id="ws-1", top_n=2)
    assert len(page1.results) == 2
    assert page1.next_cursor is not None

    # The retriever rewrites once per call; build a fresh canned rewriter for page 2.
    retriever2, _ = _retriever_with_rewrite({"text": "cybersecurity", "filters": {}})
    page2 = await retriever2.search(
        session, "cybersecurity", workspace_id="ws-1", top_n=2, cursor=page1.next_cursor
    )
    assert len(page2.results) == 1
    assert page2.next_cursor is None

    ids = {r.signal.id for r in page1.results} | {r.signal.id for r in page2.results}
    assert len(ids) == 3  # no overlap across pages
