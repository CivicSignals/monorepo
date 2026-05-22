"""Tests for embedding-based fuzzy deduplication (doc 19 §7.4; E10).

Two layers:

- Pure-unit tests (no DB): :func:`is_high_stakes_type`, :func:`_cosine_similarity`,
  :class:`FuzzyDedupeConfig`, and exception types.
- Live-DB tests (gated on ``SIGNALS_TEST_DSN`` / ``DATABASE_DIRECT_URL`` /
  ``DATABASE_URL``): end-to-end fuzzy-dedupe path — match at ≥ 0.92 → review queue
  (pre-graduation) then auto-merge (post-graduation); below 0.92 → separate;
  non-high-stakes types skip fuzzy; review approve → merge; review reject → keep;
  FakeEmbeddingBackend produces deterministic vectors.

Follows the same DB-test pattern as ``test_dedupe_db.py`` and ``test_embedding.py``:
creates/drops only the tables exercised, skips cleanly without a DSN.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import Connection, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from civicsignals_api.db import Base
from civicsignals_api.llm_gateway import FakeEmbeddingBackend, LLMGateway, ModelChoice

# Register entities models so the entities_entity FK in signals_signal resolves.
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.signals import services
from civicsignals_api.modules.signals.fuzzy_dedupe import (
    DEFAULT_FUZZY_CONFIG,
    FuzzyDedupeConfig,
    FuzzyReviewAlreadyDecidedError,
    FuzzyReviewNotFoundError,
    _cosine_similarity,
    apply_fuzzy_review,
    create_fuzzy_review,
    find_fuzzy_duplicate,
    is_high_stakes_type,
    run_fuzzy_dedupe,
    should_auto_merge,
)
from civicsignals_api.modules.signals.models import EMBEDDING_DIM, SIGNAL_STATUS_MERGED, Signal
from civicsignals_api.modules.signals.models_fuzzy_review import (
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    SignalFuzzyReview,
)
from civicsignals_api.modules.signals.schemas import SignalType
from civicsignals_api.modules.signals.services import CandidateInput

# ---------------------------------------------------------------------------
# Pure-unit tests (no DB)
# ---------------------------------------------------------------------------


def test_is_high_stakes_rfp_posted() -> None:
    assert is_high_stakes_type(SignalType.RFP_POSTED) is True


def test_is_high_stakes_contract_expiring() -> None:
    assert is_high_stakes_type(SignalType.CONTRACT_EXPIRING) is True


def test_is_not_high_stakes_budget() -> None:
    assert is_high_stakes_type(SignalType.BUDGET_APPROVED) is False


def test_is_not_high_stakes_news() -> None:
    assert is_high_stakes_type(SignalType.NEWS_MENTION) is False


def test_is_not_high_stakes_leadership() -> None:
    assert is_high_stakes_type(SignalType.LEADERSHIP_CHANGE) is False


def test_custom_config_high_stakes() -> None:
    cfg = FuzzyDedupeConfig(high_stakes=frozenset({SignalType.BUDGET_APPROVED}))
    assert is_high_stakes_type(SignalType.BUDGET_APPROVED, cfg) is True
    assert is_high_stakes_type(SignalType.RFP_POSTED, cfg) is False


def test_cosine_similarity_identical() -> None:
    v = [1.0, 0.0, 0.0]
    assert _cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal() -> None:
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert _cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_empty() -> None:
    assert _cosine_similarity([], []) == 0.0


def test_cosine_similarity_length_mismatch() -> None:
    assert _cosine_similarity([1.0], [1.0, 0.0]) == 0.0


def test_fuzzy_config_defaults() -> None:
    assert DEFAULT_FUZZY_CONFIG.threshold == pytest.approx(0.92)
    assert DEFAULT_FUZZY_CONFIG.graduation == 100
    assert SignalType.RFP_POSTED in DEFAULT_FUZZY_CONFIG.high_stakes
    assert SignalType.CONTRACT_EXPIRING in DEFAULT_FUZZY_CONFIG.high_stakes


# ---------------------------------------------------------------------------
# Live-DB fixtures
# ---------------------------------------------------------------------------

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
db = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

_TABLE = "signals_signal"
_REVIEW_TABLE = "signals_fuzzy_review"
_ENTITY_TABLES = ("entities_entity", "entities_geo", "entities_kind")


def _drop_cascade(conn: Connection) -> None:
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {_REVIEW_TABLE} CASCADE")
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")
    for tbl in _ENTITY_TABLES:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl} CASCADE")


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN)
    create_tables = (
        [Base.metadata.tables[name] for name in _ENTITY_TABLES]
        + [Base.metadata.tables[_TABLE]]
        + [Base.metadata.tables[_REVIEW_TABLE]]
    )
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(_drop_cascade)
        await conn.run_sync(Base.metadata.create_all, tables=create_tables, checkfirst=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_cascade)
        await engine.dispose()


def _embed_gateway(*, dim: int = EMBEDDING_DIM) -> LLMGateway:
    """A deterministic embedding gateway using FakeEmbeddingBackend."""
    backend = FakeEmbeddingBackend(provider="embed_be", dim=dim)
    return LLMGateway(
        {},
        embedding_backends={"embed_be": backend},
        embedding_choice=ModelChoice("embed_be", "fake-embed-model"),
    )


def _rfp_candidate(
    *,
    title: str = "RFP for Learning Analytics Platform",
    due_at: str = "2026-09-01T17:00:00Z",
    raw_document_id: uuid.UUID | None = None,
    confidence: float = 0.85,
    entity_id: uuid.UUID | None = None,
) -> CandidateInput:
    return CandidateInput(
        signal_type="rfp_posted",
        fields={
            "title": title,
            "summary": "RFP for a learning analytics platform.",
            "due_at": due_at,
        },
        recipe_id="wa_k12_rfps",
        raw_document_id=raw_document_id or uuid.uuid4(),
        content_hash="placeholder",
        confidence=confidence,
        entity_id=entity_id,
        occurred_at=datetime.now(UTC),
    )


def _budget_candidate(*, title: str = "FY2026 Cybersecurity Budget") -> CandidateInput:
    return CandidateInput(
        signal_type="budget_approved",
        fields={
            "title": title,
            "summary": "Cybersecurity budget approved.",
            "amount_cents": 1_000_000,
            "category": "cybersecurity",
            "fiscal_year": "FY2026",
        },
        recipe_id="wa_k12_budgets",
        raw_document_id=uuid.uuid4(),
        content_hash="placeholder",
        confidence=0.80,
        entity_id=None,
        occurred_at=datetime.now(UTC),
    )


async def _count_reviews(session: AsyncSession) -> int:
    n = await session.scalar(select(func.count()).select_from(SignalFuzzyReview))
    return int(n or 0)


async def _count_signals(session: AsyncSession) -> int:
    n = await session.scalar(select(func.count()).select_from(Signal))
    return int(n or 0)


# ---------------------------------------------------------------------------
# Non-high-stakes types skip fuzzy dedupe
# ---------------------------------------------------------------------------


@db
async def test_non_high_stakes_type_skips_fuzzy(session: AsyncSession) -> None:
    """budget_approved is not a high-stakes type — no fuzzy review row is created."""
    gw = _embed_gateway()
    cfg = FuzzyDedupeConfig(threshold=0.92, graduation=100)

    s1 = await services.promote_candidate_to_signal(
        session, _budget_candidate(title="Budget A"), fuzzy_config=cfg
    )
    await session.flush()
    # embed s1 so the ANN query has something to compare against
    s1.vector_embedding = (await gw.embed(["Budget A budget approved."])).vectors[0]
    await session.flush()

    # A nearly-identical budget signal should NOT trigger fuzzy dedupe.
    await services.promote_candidate_to_signal(
        session, _budget_candidate(title="Budget A"), fuzzy_config=cfg
    )
    await session.commit()

    # Two distinct signals (different exact keys if titles differ — but even with
    # identical titles budget_approved keys on fiscal_year+category, which match,
    # so it may have exact-deduped; either way, no review row should exist).
    assert await _count_reviews(session) == 0


# ---------------------------------------------------------------------------
# Fuzzy match ≥ 0.92 → review queue (before graduation)
# ---------------------------------------------------------------------------


@db
async def test_fuzzy_match_routes_to_review_pre_graduation(session: AsyncSession) -> None:
    """Fuzzy match at ≥ threshold before graduation → routed to review queue.

    Uses FakeEmbeddingBackend: identical text → identical vector → cosine 1.0 ≥ 0.92.
    The two RFPs have slightly different titles (so exact dedupe misses) but the same
    summary, which the FakeBackend turns into a very similar vector.
    """
    gw = _embed_gateway()
    cfg = FuzzyDedupeConfig(threshold=0.01, graduation=100)  # low threshold to guarantee match

    # Store the existing signal and embed it.
    existing = await services.promote_candidate_to_signal(
        session, _rfp_candidate(title="ERP RFP for Austin ISD", due_at="2026-09-01T17:00:00Z")
    )
    await session.flush()
    existing_text = "ERP RFP for Austin ISD RFP for a learning analytics platform."
    existing.vector_embedding = (await gw.embed([existing_text])).vectors[0]
    await session.commit()

    # A candidate with a slightly different title but similar content (same due_at
    # means exact key differs only on title — store path recomputes from payload).
    candidate_input = _rfp_candidate(title="ERP RFP — Austin ISD", due_at="2026-10-01T17:00:00Z")
    candidate_sig = await services.promote_candidate_to_signal(
        session, candidate_input, fuzzy_config=cfg
    )
    await session.flush()
    # Embed the candidate with text close to the existing signal.
    candidate_sig.vector_embedding = (await gw.embed([existing_text])).vectors[0]
    await session.flush()

    # Manually run fuzzy dedupe (the service already ran it, but without an
    # embedding on the candidate at that moment — let's test the standalone path).
    result = await run_fuzzy_dedupe(
        session,
        candidate_signal=candidate_sig,
        signal_type=SignalType.RFP_POSTED,
        new_doc_ids=[candidate_input.raw_document_id],
        new_confidence=0.85,
        gateway=gw,
        config=cfg,
    )
    await session.commit()

    assert result.matched is True
    assert result.routed_to_review is True
    assert result.review_id is not None
    assert await _count_reviews(session) >= 1


# ---------------------------------------------------------------------------
# Fuzzy match below threshold → no match, separate signals
# ---------------------------------------------------------------------------


@db
async def test_fuzzy_below_threshold_no_match(session: AsyncSession) -> None:
    """Vectors below the threshold do not trigger a fuzzy match."""
    gw = _embed_gateway()
    # Set threshold very high (1.0 — nothing matches except identical vectors).
    cfg = FuzzyDedupeConfig(threshold=1.0, graduation=100)

    existing = await services.promote_candidate_to_signal(
        session, _rfp_candidate(title="ERP RFP for Austin ISD", due_at="2026-09-01T17:00:00Z")
    )
    await session.flush()
    existing.vector_embedding = (
        await gw.embed(["ERP RFP for Austin ISD RFP for a learning analytics platform."])
    ).vectors[0]
    await session.commit()

    candidate = Signal(
        id=uuid.uuid4(),
        signal_type=SignalType.RFP_POSTED.value,
        recipe_id="r",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash="different-hash",
        observed_at=datetime.now(UTC),
        title="Completely different cafeteria renovation RFP",
        summary="Cafeteria renovation project RFP for school district.",
        details={},
        confidence=0.80,
    )
    session.add(candidate)
    await session.flush()
    candidate.vector_embedding = (
        await gw.embed(["Completely different cafeteria renovation RFP school district."])
    ).vectors[0]
    await session.commit()

    result = await find_fuzzy_duplicate(
        session,
        embedding=list(candidate.vector_embedding),
        signal_type=SignalType.RFP_POSTED,
        entity_id=None,
        config=cfg,
    )
    # With threshold=1.0, only an exact-identical vector matches.
    # The two different texts produce different vectors, so no match.
    assert result is None or result.id == candidate.id  # may exclude self


# ---------------------------------------------------------------------------
# Auto-merge after graduation
# ---------------------------------------------------------------------------


@db
async def test_auto_merge_after_graduation(session: AsyncSession) -> None:
    """After graduation, a fuzzy match triggers auto-merge (no review row created)."""
    gw = _embed_gateway()
    signal_type = SignalType.RFP_POSTED
    # Simulate a graduated type: insert graduation+1 reviewed rows.
    graduation = 2
    cfg = FuzzyDedupeConfig(threshold=0.01, graduation=graduation)

    # Insert dummy reviewed rows to satisfy graduation.
    dummy_cid = uuid.uuid4()
    dummy_mid = uuid.uuid4()
    for _ in range(graduation):
        row = SignalFuzzyReview(
            id=uuid.uuid4(),
            candidate_signal_id=dummy_cid,
            matched_signal_id=dummy_mid,
            similarity=0.95,
            signal_type=signal_type.value,
            status=REVIEW_STATUS_APPROVED,
        )
        session.add(row)
    await session.commit()

    graduated = await should_auto_merge(session, signal_type, config=cfg)
    assert graduated is True

    # Store existing signal + embed.
    existing = await services.promote_candidate_to_signal(
        session, _rfp_candidate(title="ERP RFP — Northshore", due_at="2026-09-01T17:00:00Z")
    )
    await session.flush()
    embed_text = "ERP RFP Northshore RFP for a learning analytics platform."
    existing.vector_embedding = (await gw.embed([embed_text])).vectors[0]
    existing_doc_count = len(existing.raw_document_ids)
    await session.commit()

    # Candidate with different title/due_at (exact miss) but same embedding text.
    candidate = Signal(
        id=uuid.uuid4(),
        signal_type=signal_type.value,
        recipe_id="r",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash="candidate-hash",
        observed_at=datetime.now(UTC),
        title="ERP RFP — Northshore ISD",
        summary="ERP RFP for a learning analytics platform.",
        details={},
        confidence=0.80,
    )
    session.add(candidate)
    await session.flush()
    candidate.vector_embedding = (await gw.embed([embed_text])).vectors[0]
    await session.commit()

    new_doc_id = uuid.UUID(candidate.raw_document_ids[0])
    result = await run_fuzzy_dedupe(
        session,
        candidate_signal=candidate,
        signal_type=signal_type,
        new_doc_ids=[new_doc_id],
        new_confidence=0.90,
        gateway=gw,
        config=cfg,
    )
    await session.commit()

    assert result.matched is True
    assert result.auto_merged is True
    assert result.routed_to_review is False

    # The matched signal gained the candidate's source document.
    refreshed = await session.get(Signal, existing.id)
    assert refreshed is not None
    assert len(refreshed.raw_document_ids) == existing_doc_count + 1

    # Auto-merged candidate must be soft-deleted: status=merged, not in list_signals.
    # (E10 recovery fix: Copilot finding — auto-merged row was left un-hidden.)
    refreshed_candidate = await session.get(Signal, candidate.id)
    assert refreshed_candidate is not None
    assert refreshed_candidate.status == SIGNAL_STATUS_MERGED
    assert refreshed_candidate.merged_into == existing.id

    page = await services.list_signals(session)
    listed_ids = {str(s.id) for s in page.items}
    assert str(candidate.id) not in listed_ids, (
        "auto-merged candidate must not appear in list_signals"
    )
    assert str(existing.id) in listed_ids, "surviving signal must still appear in list_signals"


# ---------------------------------------------------------------------------
# Review approve → merge
# ---------------------------------------------------------------------------


@db
async def test_review_approve_merges_signals(session: AsyncSession) -> None:
    """Approving a fuzzy review merges the candidate into the matched signal."""
    # Store two signals.
    existing = await services.promote_candidate_to_signal(
        session, _rfp_candidate(title="ERP RFP — approve test", due_at="2026-09-01T17:00:00Z")
    )
    await session.flush()
    existing_doc_ids_before = list(existing.raw_document_ids)

    candidate = Signal(
        id=uuid.uuid4(),
        signal_type=SignalType.RFP_POSTED.value,
        recipe_id="r",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash="candidate-approve",
        observed_at=datetime.now(UTC),
        title="ERP RFP — approve candidate",
        summary="s",
        details={},
        confidence=0.80,
    )
    session.add(candidate)
    await session.flush()

    # Create a review row.
    review = await create_fuzzy_review(
        session,
        candidate_signal_id=candidate.id,
        matched_signal_id=existing.id,
        similarity=0.95,
        signal_type=SignalType.RFP_POSTED,
    )
    await session.commit()

    assert review.status == REVIEW_STATUS_PENDING

    # Approve.
    updated = await apply_fuzzy_review(
        session, review.id, approved=True, reviewer_note="Looks right"
    )
    await session.commit()

    assert updated.status == REVIEW_STATUS_APPROVED
    assert updated.reviewed_at is not None
    assert updated.reviewer_note == "Looks right"

    # Existing signal now has the candidate's source doc.
    refreshed = await session.get(Signal, existing.id)
    assert refreshed is not None
    all_doc_ids = set(refreshed.raw_document_ids)
    assert all_doc_ids.issuperset(set(existing_doc_ids_before))
    assert set(candidate.raw_document_ids).issubset(all_doc_ids)

    # Candidate row is soft-deleted: status=merged, merged_into points to survivor.
    refreshed_candidate = await session.get(Signal, candidate.id)
    assert refreshed_candidate is not None
    assert refreshed_candidate.status == SIGNAL_STATUS_MERGED
    assert refreshed_candidate.merged_into == existing.id

    # list_signals must NOT include the merged candidate (E10 recovery fix: Copilot
    # finding — approved merge left the duplicate row surfacing in results).
    page = await services.list_signals(session)
    listed_ids = {str(s.id) for s in page.items}
    assert str(candidate.id) not in listed_ids, "merged candidate must not appear in list_signals"
    assert str(existing.id) in listed_ids, "surviving signal must still appear in list_signals"


# ---------------------------------------------------------------------------
# Review reject → keep separate
# ---------------------------------------------------------------------------


@db
async def test_review_reject_keeps_separate(session: AsyncSession) -> None:
    """Rejecting a fuzzy review keeps the candidate as a distinct signal."""
    existing = await services.promote_candidate_to_signal(
        session, _rfp_candidate(title="ERP RFP — reject test", due_at="2026-09-01T17:00:00Z")
    )
    await session.flush()
    existing_docs_before = list(existing.raw_document_ids)

    candidate = Signal(
        id=uuid.uuid4(),
        signal_type=SignalType.RFP_POSTED.value,
        recipe_id="r",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash="candidate-reject",
        observed_at=datetime.now(UTC),
        title="ERP RFP — reject candidate",
        summary="s",
        details={},
        confidence=0.80,
    )
    session.add(candidate)
    await session.flush()

    review = await create_fuzzy_review(
        session,
        candidate_signal_id=candidate.id,
        matched_signal_id=existing.id,
        similarity=0.93,
        signal_type=SignalType.RFP_POSTED,
    )
    await session.commit()

    updated = await apply_fuzzy_review(
        session, review.id, approved=False, reviewer_note="Different"
    )
    await session.commit()

    assert updated.status == REVIEW_STATUS_REJECTED
    # Existing signal is NOT modified — candidate stays separate.
    refreshed = await session.get(Signal, existing.id)
    assert refreshed is not None
    assert list(refreshed.raw_document_ids) == existing_docs_before


# ---------------------------------------------------------------------------
# Error cases: review not found, already decided
# ---------------------------------------------------------------------------


@db
async def test_fuzzy_review_not_found_raises(session: AsyncSession) -> None:
    with pytest.raises(FuzzyReviewNotFoundError):
        await apply_fuzzy_review(session, uuid.uuid4(), approved=True)


@db
async def test_fuzzy_review_already_decided_raises(session: AsyncSession) -> None:
    existing = await services.promote_candidate_to_signal(
        session, _rfp_candidate(title="ERP RFP — double-decide", due_at="2026-09-01T17:00:00Z")
    )
    await session.flush()
    candidate = Signal(
        id=uuid.uuid4(),
        signal_type=SignalType.RFP_POSTED.value,
        recipe_id="r",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash="c-dd",
        observed_at=datetime.now(UTC),
        title="ERP RFP — double-decide-candidate",
        summary="s",
        details={},
        confidence=0.80,
    )
    session.add(candidate)
    await session.flush()
    review = await create_fuzzy_review(
        session,
        candidate_signal_id=candidate.id,
        matched_signal_id=existing.id,
        similarity=0.94,
        signal_type=SignalType.RFP_POSTED,
    )
    await session.commit()

    await apply_fuzzy_review(session, review.id, approved=True)
    await session.commit()

    with pytest.raises(FuzzyReviewAlreadyDecidedError):
        await apply_fuzzy_review(session, review.id, approved=False)


# ---------------------------------------------------------------------------
# Graduation count: pending rows don't count
# ---------------------------------------------------------------------------


@db
async def test_graduation_only_counts_decided_rows(session: AsyncSession) -> None:
    """Pending rows do not count toward graduation (only decided rows do)."""
    graduation = 2
    cfg = FuzzyDedupeConfig(threshold=0.92, graduation=graduation)
    signal_type = SignalType.RFP_POSTED

    # Add graduation-1 approved rows (not yet graduated).
    for _ in range(graduation - 1):
        session.add(
            SignalFuzzyReview(
                id=uuid.uuid4(),
                candidate_signal_id=uuid.uuid4(),
                matched_signal_id=uuid.uuid4(),
                similarity=0.95,
                signal_type=signal_type.value,
                status=REVIEW_STATUS_APPROVED,
            )
        )
    # Add a pending row — should NOT count.
    session.add(
        SignalFuzzyReview(
            id=uuid.uuid4(),
            candidate_signal_id=uuid.uuid4(),
            matched_signal_id=uuid.uuid4(),
            similarity=0.93,
            signal_type=signal_type.value,
            status=REVIEW_STATUS_PENDING,
        )
    )
    await session.commit()

    not_graduated = await should_auto_merge(session, signal_type, config=cfg)
    assert not_graduated is False

    # Add the final approved row to reach graduation.
    session.add(
        SignalFuzzyReview(
            id=uuid.uuid4(),
            candidate_signal_id=uuid.uuid4(),
            matched_signal_id=uuid.uuid4(),
            similarity=0.94,
            signal_type=signal_type.value,
            status=REVIEW_STATUS_APPROVED,
        )
    )
    await session.commit()

    graduated = await should_auto_merge(session, signal_type, config=cfg)
    assert graduated is True


# ---------------------------------------------------------------------------
# ANN query: window boundary respected
# ---------------------------------------------------------------------------


@db
async def test_fuzzy_query_respects_window(session: AsyncSession) -> None:
    """A signal outside the type window is not returned by the ANN query."""
    gw = _embed_gateway()
    cfg = FuzzyDedupeConfig(threshold=0.01, graduation=100)
    signal_type = SignalType.RFP_POSTED

    # Insert an old signal (just outside the 90d RFP window).
    now = datetime.now(UTC)
    embed_text = "ERP RFP window test learning analytics."
    old_vec = (await gw.embed([embed_text])).vectors[0]

    old = Signal(
        id=uuid.uuid4(),
        signal_type=signal_type.value,
        recipe_id="r",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash="old-hash",
        occurred_at=now - timedelta(days=91),
        observed_at=now - timedelta(days=91),
        title="ERP RFP window test",
        summary="learning analytics.",
        details={},
        confidence=0.80,
        vector_embedding=old_vec,
    )
    session.add(old)
    await session.commit()

    # An ANN query with the same vector should NOT find the old signal (outside window).
    result = await find_fuzzy_duplicate(
        session,
        embedding=old_vec,
        signal_type=signal_type,
        entity_id=None,
        now=now,
        config=cfg,
    )
    assert result is None


# ---------------------------------------------------------------------------
# HTTP admin gate tests (no DB required — dependency override)
# ---------------------------------------------------------------------------
# These tests verify that the fuzzy-review endpoints return 401 when
# unauthenticated and 403 when the user lacks admin role. They use FastAPI's
# TestClient with dependency overrides so no live DB is needed.
# (E10 recovery fix: Copilot finding — endpoints had NO auth dependency.)
# ---------------------------------------------------------------------------


def test_fuzzy_reviews_list_requires_auth() -> None:
    """GET /signals/fuzzy-reviews returns 401 when no bearer token is provided."""
    from fastapi.testclient import TestClient

    from civicsignals_api.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get("/api/v1/signals/fuzzy-reviews")
    assert resp.status_code == 401


def test_fuzzy_review_get_requires_auth() -> None:
    """GET /signals/fuzzy-reviews/{id} returns 401 when unauthenticated."""
    from fastapi.testclient import TestClient

    from civicsignals_api.main import app

    fake_id = uuid.uuid4()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.get(f"/api/v1/signals/fuzzy-reviews/{fake_id}")
    assert resp.status_code == 401


def test_fuzzy_review_approve_requires_auth() -> None:
    """POST /signals/fuzzy-reviews/{id}/approve returns 401 when unauthenticated."""
    from fastapi.testclient import TestClient

    from civicsignals_api.main import app

    fake_id = uuid.uuid4()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(f"/api/v1/signals/fuzzy-reviews/{fake_id}/approve")
    assert resp.status_code == 401


def test_fuzzy_review_reject_requires_auth() -> None:
    """POST /signals/fuzzy-reviews/{id}/reject returns 401 when unauthenticated."""
    from fastapi.testclient import TestClient

    from civicsignals_api.main import app

    fake_id = uuid.uuid4()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(f"/api/v1/signals/fuzzy-reviews/{fake_id}/reject")
    assert resp.status_code == 401


def test_fuzzy_review_list_non_admin_gets_403_or_404() -> None:
    """Non-admin member cannot access fuzzy-review endpoints (E10 security fix).

    Uses a fake ``require_workspace`` that returns a viewer-role context so the
    RequireAdmin dependency sees an insufficient role → 403. (A non-member
    yields 404 — both are acceptable and better than 200.)
    """
    from typing import Any
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from civicsignals_api.main import app
    from civicsignals_api.modules.accounts.models import MembershipRole
    from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace

    fake_workspace = MagicMock()
    fake_workspace.id = uuid.uuid4()
    fake_membership = MagicMock()
    fake_membership.role = MembershipRole.VIEWER

    viewer_ctx = WorkspaceContext(
        user=MagicMock(),
        workspace=fake_workspace,
        membership=fake_membership,
    )

    def _viewer_ctx() -> Any:
        return viewer_ctx

    app.dependency_overrides[require_workspace] = _viewer_ctx
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/api/v1/signals/fuzzy-reviews",
                headers={"X-Workspace-Id": str(fake_workspace.id)},
            )
        assert resp.status_code in (403, 404), (
            f"Expected 403 or 404 for non-admin, got {resp.status_code}: {resp.text}"
        )
    finally:
        app.dependency_overrides.pop(require_workspace, None)
