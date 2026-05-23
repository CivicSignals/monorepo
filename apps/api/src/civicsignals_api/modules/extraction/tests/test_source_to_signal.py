# SPDX-License-Identifier: AGPL-3.0-only
"""Layer B — source → signal → per-workspace routing conversion tests (e2e analytics).

These tie the analytics chain together end-to-end, *synchronously*, against a real
Postgres (resolved from ``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` /
``SIGNALS_TEST_DSN``) with a deterministic ``FakeBackend`` LLM gateway. They drive the
**production** orchestrator ``extraction.pipeline.run_extraction_pipeline`` over a
seeded raw document and assert the conversion correctness at each funnel stage and the
F3 routing fan-out — the thing the Playwright feed-routing e2e proves through the UI,
proven here at the service layer.

What this file adds (vs. what is already covered — cited inline, not duplicated):

- **The full chain glue** (fetch → parse → relevance gate → extract → score →
  dedupe → store → embed → score-for-all-workspaces) against the real DB with a
  *resolved* entity. The unit/orchestrator slices live in
  ``test_pipeline_run.py`` (stubbed promote) + ``test_pipeline_persistence.py``
  (entity_id=None); this is the missing integration where an extracted entity links
  to an ``entities_entity`` row (E11) AND the strict gate accepts a resolved-entity
  candidate (E4 ``entity_name``-extra regression) AND the band is computed (E6) AND
  the produced signal routes to the matching workspace only (F3).
- **E5 outside-window → distinct signals** (the inside-window merge is covered by
  ``signals/tests/test_services_db.py::test_repromotion_is_idempotent_and_merges_docs``;
  the outside-window split was a gap).
- **E10 fuzzy near-duplicate → review** via the *service* promotion path
  ``promote_candidate_to_signal`` (the standalone ``run_fuzzy_dedupe`` call is
  covered by ``signals/tests/test_fuzzy_dedupe.py``; routing it through the public
  store seam end-to-end was a gap).
- **The routing invariant** with two disjoint-ICP workspaces over a
  *pipeline-produced* signal — the service-layer complement to the Playwright e2e.

Behaviours already well-covered and intentionally NOT re-tested here:

- E8 relevance gate drop / low-confidence drop / pass — ``test_pipeline_run.py``.
- E6 band boundaries (pure math, all four bands) — ``signals/tests/test_scoring.py``;
  the in-pipeline degraded band — ``test_pipeline_persistence.py``.
- The matcher predicate + scorer blend (pure) — ``signals/tests/test_workspace_scoring.py``.
- The DB fan-out / workspace-isolation primitives — ``signals/tests/test_workspace_score_db.py``.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base
from civicsignals_api.llm_gateway import (
    TASK_CLASSIFY,
    TASK_EXTRACTION,
    FakeBackend,
    FakeEmbeddingBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)

# Import every owning module's models so ``Base.metadata`` is complete for
# create_all: the chain touches entities + signals (+ fuzzy review) + icp +
# accounts (workspace) + the workspace-score table.
from civicsignals_api.modules.accounts.models import (  # noqa: F401
    Membership,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.entities import services as entities_services
from civicsignals_api.modules.extraction import pipeline
from civicsignals_api.modules.extraction.models import ExtractionCandidate
from civicsignals_api.modules.extraction.relevance import RelevanceClassifier
from civicsignals_api.modules.icp.models import IcpDefinition
from civicsignals_api.modules.ingestion.services import StoredRawDocument
from civicsignals_api.modules.signals import services as signals_services
from civicsignals_api.modules.signals.models import (
    EMBEDDING_DIM,
    SIGNAL_STATUS_MERGED,
    SIGNAL_STATUS_NEW,
    SIGNAL_STATUS_PENDING_REVIEW,
    Signal,
)
from civicsignals_api.modules.signals.models_fuzzy_review import (
    REVIEW_STATUS_PENDING,
    SignalFuzzyReview,
)
from civicsignals_api.modules.signals.schemas import SignalType
from civicsignals_api.modules.signals.services import CandidateInput
from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore

_DSN = (
    os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
    or os.environ.get("SIGNALS_TEST_DSN")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

# A fixed reference time so recency / window arithmetic is deterministic.
NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# DB fixture — full schema (the chain spans many modules' tables)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A clean full-schema session per test.

    The chain spans many modules' tables with a web of cross-FKs (e.g.
    ``contacts_contact`` / ``ingestion_raw_document`` / ``foia_request`` all
    FK-reference ``entities_entity``). ``Base.metadata.drop_all`` cannot always
    topologically order a drop against a *pre-migrated* DB, so we ``DROP SCHEMA ...
    CASCADE`` for a clean slate (the throwaway container is dedicated to these
    tests), re-enable the extensions, and ``create_all`` the ORM-declared schema.
    """
    assert _DSN is not None
    engine = create_async_engine(_DSN, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()


# ---------------------------------------------------------------------------
# Fake storage + seeded raw document (no S3, no network)
# ---------------------------------------------------------------------------


class _FakeStorage:
    """Network-free ``RawDocumentStorage`` returning fixed bytes for any key."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def get_document(self, key: str) -> bytes:
        return self._content


def _stored_doc(
    *,
    doc_id: uuid.UUID,
    recipe_id: str = "tx_k12_rfps",
    source_url: str = "https://procurement.txed.example.gov/rfp/erp-2026",
) -> StoredRawDocument:
    return StoredRawDocument(
        id=doc_id,
        recipe_id=recipe_id,
        recipe_version=1,
        connector="http_static",
        source_url=source_url,
        fetched_at=NOW,
        content_hash="0" * 64,
        blob_key="sha256/" + "0" * 64,
        content_type="text/html",
    )


# ---------------------------------------------------------------------------
# Fake LLM gateway — scripted relevance / entity / signal replies
# ---------------------------------------------------------------------------
#
# Two `complete` backends so the relevance (TASK_CLASSIFY) and the extract
# (TASK_EXTRACTION) FIFO response queues never interleave (mirrors the helper in
# ``test_pipeline_run.py``). The extract backend is seeded with the entity-extraction
# Pass-1 reply *then* the signal reply, in the order the pipeline calls them.


def _entity_reply(org_name: str) -> str:
    """A Pass-1 entity reply with >= 3 entity types so no Pass 2 fires.

    ``org_name`` is the organisation mention the entity-linking step (E11) looks up in
    ``entities_entity`` via ``entities.services.search_entities``.
    """
    return json.dumps(
        {
            "organizations": [{"name": org_name, "confidence": 0.9}],
            "persons": [{"name": "Jane Smith", "role": "CTO", "confidence": 0.85}],
            "monetary_amounts": [{"amount_cents": 40000000, "currency": "USD", "confidence": 0.9}],
            "dates": [{"value": "2026-06-01", "kind": "due_date", "confidence": 0.88}],
            "products_categories": ["ERP"],
            "vendors_mentioned": [],
            "contract_terms_mentions": [],
            "raw_keywords": ["procurement"],
            "extraction_confidence": 0.85,
            "extraction_warnings": [],
        }
    )


def _gateway(
    *,
    relevance: str,
    entity: str,
    signal: str,
) -> tuple[LLMGateway, FakeBackend]:
    """Gateway whose classify + extract tasks return scripted JSON, network-free.

    Returns the gateway plus the extract backend so a test can assert whether the
    expensive extract task ever fired (the E8 cost lever).
    """
    extract_be = FakeBackend(provider="extract_be", responses=[entity, signal])
    policy = TaskModelPolicy(
        overrides={
            TASK_CLASSIFY: ModelChoice("relevance_be", "haiku"),
            TASK_EXTRACTION: ModelChoice("extract_be", "sonnet"),
        }
    )
    gateway = LLMGateway(
        {
            "relevance_be": FakeBackend(provider="relevance_be", responses=[relevance]),
            "extract_be": extract_be,
        },
        policy=policy,
        # The store stage embeds each promoted signal (I1); a deterministic fake
        # embedding backend keeps that network-free and dimensioned to the column.
        embedding_backends={
            "fake_embed": FakeEmbeddingBackend(provider="fake_embed", dim=EMBEDDING_DIM)
        },
        embedding_choice=ModelChoice("fake_embed", "fake-embed-model"),
    )
    return gateway, extract_be


def _relevant(confidence: float = 0.9, categories: list[str] | None = None) -> str:
    return json.dumps(
        {"relevant": True, "categories": categories or ["procurement"], "confidence": confidence}
    )


def _signal_candidates(candidates: list[dict[str, object]]) -> str:
    return json.dumps({"candidates": candidates})


async def _run_pipeline(
    session: AsyncSession,
    *,
    gateway: LLMGateway,
    doc: StoredRawDocument,
    body: bytes,
    monkeypatch: pytest.MonkeyPatch,
    job_id: uuid.UUID | None = None,
) -> pipeline.PipelineResult:
    """Run the real orchestrator for one seeded document (no Celery, no network)."""

    async def _fake_get(_session: object, _document_id: uuid.UUID) -> StoredRawDocument:
        return doc

    monkeypatch.setattr(
        "civicsignals_api.modules.extraction.pipeline.ingestion_services.get_raw_document",
        _fake_get,
    )
    return await pipeline.run_extraction_pipeline(
        session,
        _FakeStorage(body),  # type: ignore[arg-type]
        job_id=job_id or uuid.uuid4(),
        raw_document_id=doc.id,
        classifier=RelevanceClassifier(gateway=gateway),
        gateway=gateway,
        prefilter="classifier",
    )


# ---------------------------------------------------------------------------
# Workspace / ICP / entity builders (inline; the seed_e2e harness encodes the same
# routing invariant but we build minimal rows here for tight, fast assertions).
# ---------------------------------------------------------------------------


async def _workspace(session: AsyncSession, *, email: str) -> uuid.UUID:
    user = User(id=uuid.uuid4(), email=email, password_hash="x", name="T")
    org = Organization(id=uuid.uuid4(), name="Org")
    session.add_all([user, org])
    await session.flush()
    ws = Workspace(
        id=uuid.uuid4(),
        organization_id=org.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:12]}",
        owner_id=user.id,
    )
    session.add(ws)
    await session.flush()
    return ws.id


async def _icp(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    states: list[str],
    signal_types: list[str],
    entity_kinds: list[str] | None = None,
    keywords_required: list[str] | None = None,
    threshold: int = 30,
) -> IcpDefinition:
    icp = IcpDefinition(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="ICP",
        countries=["US"],
        states=states,
        entity_kinds=entity_kinds or [],
        signal_types=signal_types,
        signal_weights=dict.fromkeys(signal_types, 1.0),
        keywords_required=keywords_required or [],
        keywords_excluded=[],
        threshold=threshold,
        is_active=True,
    )
    session.add(icp)
    await session.flush()
    return icp


async def _scores(session: AsyncSession, workspace_id: uuid.UUID) -> list[WorkspaceScore]:
    rows = await session.execute(
        select(WorkspaceScore).where(WorkspaceScore.workspace_id == workspace_id)
    )
    return list(rows.scalars().all())


async def _count_signals(session: AsyncSession) -> int:
    n = await session.scalar(
        select(func.count()).select_from(Signal).where(Signal.status != SIGNAL_STATUS_MERGED)
    )
    return int(n or 0)


# ===========================================================================
# 1. The full source → signal → routing chain (the integration glue)
# ===========================================================================


async def test_source_to_signal_to_routing_end_to_end(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the real funnel over one TX-school RFP and prove every stage + the routing.

    Asserts the chain the Playwright e2e covers through the UI, at the service layer:
    - relevance gate passes (E8) and the candidate is extracted + scored;
    - the extracted org links to the pre-seeded ``entities_entity`` (E11) so the
      signal carries ``entity_id`` and is NOT held for the unresolved-entity review;
    - the strict per-type gate accepts a *resolved-entity* candidate cleanly (E4 —
      the foundation's ``entity_name``-extra regression: a resolved entity must not
      spuriously trip ``extra="forbid"``);
    - the E6 band is HIGH (status=new), and the signal is embedded (I1);
    - the fan-out writes a ``signals_workspace_score`` row in the TX/rfp workspace
      ONLY, not the disjoint CA/news workspace (F3 routing invariant).
    """
    # Pre-seed the canonical entity the RFP is about so entity linking resolves it.
    entity = await entities_services.upsert_entity(
        session,
        natural_key="nces_leaid",
        nces_leaid="4800001",
        type="school_district",
        name="Lone Star Independent School District",
        country="US",
        state="TX",
        enrollment=52000,
    )
    await session.flush()

    # Two disjoint-ICP workspaces: only Alice (TX / rfp_posted) should see the signal.
    ws_alice = await _workspace(session, email="alice.s2s@example.com")
    ws_bob = await _workspace(session, email="bob.s2s@example.com")
    await _icp(
        session,
        workspace_id=ws_alice,
        states=["TX"],
        entity_kinds=["school_district"],
        signal_types=["rfp_posted"],
        threshold=30,
    )
    await _icp(
        session,
        workspace_id=ws_bob,
        states=["CA"],
        signal_types=["news_mention"],
        threshold=30,
    )
    await session.flush()

    doc = _stored_doc(doc_id=uuid.uuid4())
    gw, extract_be = _gateway(
        relevance=_relevant(0.9),
        # The org name matches the seeded entity → E11 linking resolves it.
        entity=_entity_reply("Lone Star Independent School District"),
        signal=_signal_candidates(
            [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.9,
                    "fields": {
                        "title": "ERP modernization RFP",
                        "summary": "RFP for an ERP modernization platform.",
                        "due_at": "2026-09-01T17:00:00Z",
                        "rfp_number": "RFP-2026-ERP",
                        "amount_cents": 40000000,
                        "posting_agency": "Lone Star ISD",
                        "contact_name": "Jane Smith",
                        "submission_url": "https://procurement.txed.example.gov/submit",
                        "requirements": ["SIS integration"],
                    },
                }
            ]
        ),
    )

    result = await _run_pipeline(
        session,
        gateway=gw,
        doc=doc,
        body=b"ERP modernization RFP, due 2026-09-01",
        monkeypatch=monkeypatch,
    )

    # --- Funnel produced exactly one survivor candidate (E8 passed, not rejected) --
    assert result.skipped is False
    assert result.relevant is True
    assert len(result.candidates) == 1
    assert extract_be.calls, "extract task must have fired on a relevant document"

    # --- Exactly one signal stored, linked to the resolved entity (E11) -----------
    signals = (
        (await session.execute(select(Signal).where(Signal.recipe_id == doc.recipe_id)))
        .scalars()
        .all()
    )
    assert len(signals) == 1
    sig = signals[0]
    assert sig.signal_type == "rfp_posted"
    assert sig.title == "ERP modernization RFP"
    # E11: the extracted org resolved to the seeded entity → entity_id set, raw name kept.
    assert sig.entity_id == entity.id
    # E4 regression: a resolved-entity candidate passes the strict gate cleanly — the
    # ``entity_name`` convenience key was stripped before the validator, so the row
    # was written (no spurious SignalValidationError on the forbid-extras schema).
    assert sig.details["due_at"] == "2026-09-01T17:00:00Z"
    assert "entity_name" not in sig.details  # not leaked into the typed payload
    # E6: a fully-specified, high-confidence, resolved candidate lands in the HIGH
    # (normal) band → status=new, not held for review.
    assert sig.review_required is False
    assert sig.is_degraded is False
    assert sig.status == SIGNAL_STATUS_NEW
    # I1: the signal was embedded at extraction time.
    assert sig.vector_embedding is not None
    assert len(sig.vector_embedding) == EMBEDDING_DIM

    # --- Routing fan-out: TX workspace gets a row; CA workspace gets none (F3) ----
    await session.flush()
    written = await signals_services.score_signal_for_all_workspaces(
        session, signal_id=sig.id, now=NOW
    )
    await session.commit()

    assert written == 1, "the signal must route to exactly one (matching) workspace"
    rows_alice = await _scores(session, ws_alice)
    rows_bob = await _scores(session, ws_bob)
    assert len(rows_alice) == 1
    assert rows_alice[0].signal_id == sig.id
    assert rows_alice[0].matched_signal_type is True
    assert rows_alice[0].matched_state is True
    assert rows_bob == [], "disjoint CA/news workspace must not see the TX rfp signal"


async def test_relevance_gate_drops_below_floor_no_signal(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A below-floor relevance verdict drops the document — no signal, no extract call.

    The orchestrator-level drop is covered by ``test_pipeline_run.py`` with a stubbed
    DB; this asserts the *DB* invariant end-to-end: a low-confidence relevant verdict
    leaves ``signals_signal`` empty (the funnel's largest cost lever, doc 19 §3.2; E8).
    """
    doc = _stored_doc(doc_id=uuid.uuid4())
    gw, extract_be = _gateway(
        # relevant=True but below RELEVANCE_CONFIDENCE_FLOOR (0.6) → dropped.
        relevance=_relevant(0.4),
        entity="SHOULD NOT BE CALLED",
        signal="SHOULD NOT BE CALLED",
    )

    result = await _run_pipeline(
        session,
        gateway=gw,
        doc=doc,
        body=b"maybe relevant municipal notice",
        monkeypatch=monkeypatch,
    )
    await session.commit()

    assert result.skipped is True
    assert result.candidates == []
    assert extract_be.calls == [], "extract must not fire below the relevance floor"
    assert await _count_signals(session) == 0
    candidate_rows = await session.scalar(select(func.count()).select_from(ExtractionCandidate))
    assert int(candidate_rows or 0) == 0


# ===========================================================================
# 2. Confidence banding (E6) — the REJECTED band never persists a signal
# ===========================================================================
#
# The four band boundaries (HIGH/DEGRADED/PENDING_REVIEW/REJECTED) are exhaustively
# covered as pure math in ``signals/tests/test_scoring.py::test_band_boundaries`` and
# the in-pipeline DEGRADED band is covered in ``test_pipeline_persistence.py``. The
# gap is the *funnel-level* invariant that a REJECTED-band candidate is dropped before
# store (doc 19 §6.3) so it never reaches ``signals_signal`` at all.


async def test_rejected_band_candidate_never_persists(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate scoring in the REJECTED band is dropped before store (no signal).

    A leadership_change with the minimum fields, self-reported 0.05, an unresolved
    entity and no cross-validation blends well below the 0.4 pending-review floor →
    REJECTED band → filtered out of the survivors → never promoted.
    """
    doc = _stored_doc(doc_id=uuid.uuid4(), recipe_id="board_minutes")
    gw, _extract_be = _gateway(
        relevance=_relevant(0.9),
        # No org match seeded → entity stays unresolved (feeds the low cross-validation).
        entity=_entity_reply("Some Unknown Body"),
        signal=_signal_candidates(
            [
                {
                    "signal_type": "leadership_change",
                    "confidence": 0.05,
                    "fields": {
                        "title": "x",
                        "summary": "y",
                        "role": "CIO",
                        "person_name": "A. Doe",
                    },
                }
            ]
        ),
    )

    result = await _run_pipeline(
        session, gateway=gw, doc=doc, body=b"board notes", monkeypatch=monkeypatch
    )
    await session.commit()

    # The document was relevant + extracted, but the only candidate fell in REJECTED.
    assert result.relevant is True
    assert result.skipped is False
    assert result.candidates == [], "rejected-band candidate must be dropped before store"
    assert await _count_signals(session) == 0


async def test_degraded_band_candidate_persists_with_flag(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DEGRADED-band candidate persists but carries the degraded/review flags (E6).

    Complements the REJECTED case above and the persistence test's degraded assertion:
    here we confirm at the funnel level that a mid-band candidate is NOT dropped (it is
    a survivor) and lands ``is_degraded`` + held for review.
    """
    doc = _stored_doc(doc_id=uuid.uuid4(), recipe_id="degraded_rfps")
    gw, _extract_be = _gateway(
        relevance=_relevant(0.9),
        entity=_entity_reply("Unseeded District"),  # unresolved → review + low cross-val
        signal=_signal_candidates(
            [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.8,
                    # Only required fields → 0/6 optional → low schema completeness →
                    # blend ~0.605 -> DEGRADED band (0.6 to 0.8), per the persistence test.
                    "fields": {
                        "title": "Sparse RFP",
                        "summary": "RFP with only required fields.",
                        "due_at": "2026-09-01T17:00:00Z",
                    },
                }
            ]
        ),
    )

    result = await _run_pipeline(
        session, gateway=gw, doc=doc, body=b"sparse rfp", monkeypatch=monkeypatch
    )
    await session.commit()

    assert len(result.candidates) == 1
    sig = (
        (await session.execute(select(Signal).where(Signal.recipe_id == doc.recipe_id)))
        .scalars()
        .one()
    )
    assert sig.is_degraded is True
    assert sig.review_required is True
    assert sig.status == SIGNAL_STATUS_PENDING_REVIEW
    assert sig.entity_id is None  # unresolved entity → also forces review (doc 19 §4.3)


# ===========================================================================
# 3. Strict schema gate (E4) — invalid fields dead-letter, no partial signal
# ===========================================================================
#
# The promote-level invalid case is covered in
# ``signals/tests/test_services_db.py::test_promote_invalid_candidate_writes_no_signal``.
# The gap is the *funnel-level* behaviour: a schema-violating candidate raises
# SignalValidationError out of ``run_extraction_pipeline`` (the task dead-letters) and
# the candidate row is stamped rejected — no partial signal written.


async def test_strict_gate_violation_dead_letters_no_signal(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An rfp_posted missing the required ``due_at`` raises out of the pipeline (E4).

    The candidate row is stamped ``rejected`` for the audit, the validation error
    surfaces (so the Celery task dead-letters), and NO signal is written.
    """
    doc = _stored_doc(doc_id=uuid.uuid4(), recipe_id="bad_rfps")
    gw, _extract_be = _gateway(
        relevance=_relevant(0.9),
        entity=_entity_reply("Whatever District"),
        signal=_signal_candidates(
            [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.85,
                    # Missing the required ``due_at`` → strict per-type gate rejects.
                    "fields": {"title": "No due date RFP", "summary": "missing due_at"},
                }
            ]
        ),
    )

    with pytest.raises(signals_services.SignalValidationError) as exc:
        await _run_pipeline(session, gateway=gw, doc=doc, body=b"bad rfp", monkeypatch=monkeypatch)
    await session.rollback()
    assert any("due_at" in e for e in exc.value.errors)
    # Nothing committed on the raised error → the snapshot stays replayable.
    assert await _count_signals(session) == 0


# ===========================================================================
# 4. Exact dedupe (E5) — outside the window → distinct signals
# ===========================================================================
#
# The inside-window MERGE (source docs appended, higher confidence kept) is covered by
# ``signals/tests/test_services_db.py::test_repromotion_is_idempotent_and_merges_docs``.
# The outside-window SPLIT was a gap: the same entity+type+key beyond the type window
# must produce two distinct signals, not a merge.


async def test_exact_dedupe_outside_window_is_distinct(session: AsyncSession) -> None:
    """Same entity+type+key, but observed outside the rfp_posted window → 2 signals (E5)."""
    # A real entity row so the canonical dedupe hash keys on a stable resolved id
    # across both ingests (signals_signal.entity_id is a real FK).
    entity = await entities_services.upsert_entity(
        session,
        natural_key="nces_leaid",
        nces_leaid="4800500",
        type="school_district",
        name="Recurring RFP ISD",
        country="US",
        state="TX",
    )
    await session.flush()

    def _candidate(*, occurred_at: datetime) -> CandidateInput:
        # entity_id pinned (resolved) so the canonical dedupe hash is identical across
        # both ingests — only the windowed lookup time differs.
        return CandidateInput(
            signal_type="rfp_posted",
            fields={
                "title": "Recurring annual ERP RFP",
                "summary": "The district re-posts its ERP RFP each cycle.",
                "due_at": "2026-09-01T17:00:00Z",
            },
            recipe_id="tx_k12_rfps",
            raw_document_id=uuid.uuid4(),
            content_hash="ignored-store-recomputes",
            entity_id=entity.id,
            confidence=0.85,
            occurred_at=occurred_at,
        )

    # rfp_posted window is 90 days (signals.dedupe.DEDUPE_WINDOWS). Place the two
    # ingests ~200 days apart so the second falls outside the first's window.
    first = _candidate(occurred_at=NOW - timedelta(days=210))
    second = _candidate(occurred_at=NOW)

    s1 = await signals_services.promote_candidate_to_signal(session, first)
    await session.commit()
    s2 = await signals_services.promote_candidate_to_signal(session, second)
    await session.commit()

    assert s2.id != s1.id, "ingests outside the type window must NOT merge"
    assert await _count_signals(session) == 2
    # Same canonical content hash (same entity+type+key) but distinct rows by window.
    assert s1.content_hash == s2.content_hash


# ===========================================================================
# 5. Fuzzy dedupe (E10) — near-duplicate routes to review via the promote seam
# ===========================================================================
#
# The standalone ``run_fuzzy_dedupe`` paths (review / auto-merge / below-threshold /
# window) are covered in ``signals/tests/test_fuzzy_dedupe.py``. The gap is routing it
# through the public ``promote_candidate_to_signal`` store seam — i.e. that a
# high-stakes near-duplicate that misses exact dedupe but matches on embedding is
# routed to the human review queue (pre-graduation) rather than silently merged.


async def test_fuzzy_near_duplicate_routes_to_review_via_promote(session: AsyncSession) -> None:
    """A high-stakes (rfp_posted) embedding near-duplicate → fuzzy review row (E10).

    Both RFPs share the same entity + similar text but differ on the exact key
    (different ``due_at``), so exact dedupe (E5) misses. The fuzzy layer (doc 19 §7.4)
    embeds them, finds a cosine match within the (lowered) threshold, and — because the
    type has not graduated — routes the second to the ``signals_fuzzy_review`` queue.
    The promote seam embeds via the gateway, so we inject a deterministic one.
    """
    from civicsignals_api.modules.signals.fuzzy_dedupe import FuzzyDedupeConfig

    gw = LLMGateway(
        {},
        embedding_backends={
            "embed_be": FakeEmbeddingBackend(provider="embed_be", dim=EMBEDDING_DIM)
        },
        embedding_choice=ModelChoice("embed_be", "fake-embed-model"),
    )
    # Low threshold guarantees the FakeEmbeddingBackend vectors clear it; not graduated.
    cfg = FuzzyDedupeConfig(threshold=0.01, graduation=100)
    entity = await entities_services.upsert_entity(
        session,
        natural_key="nces_leaid",
        nces_leaid="4800600",
        type="school_district",
        name="Austin ISD",
        country="US",
        state="TX",
    )
    await session.flush()
    shared_entity = entity.id

    def _rfp(*, title: str, due_at: str) -> CandidateInput:
        return CandidateInput(
            signal_type="rfp_posted",
            fields={
                "title": title,
                "summary": "RFP for a learning analytics platform.",
                "due_at": due_at,
            },
            recipe_id="wa_k12_rfps",
            raw_document_id=uuid.uuid4(),
            content_hash="placeholder",
            entity_id=shared_entity,
            confidence=0.85,
            occurred_at=NOW,
        )

    # The FakeEmbeddingBackend hashes each text into a (near-orthogonal) random unit
    # vector, so to model a *near-duplicate* we embed both signals from the SAME text
    # → identical vector → cosine 1.0 (mirrors test_fuzzy_dedupe.py). Both promotes use
    # the DEFAULT fuzzy config so the inline store-path fuzzy step (which calls
    # get_gateway()) does not match here; we drive the fuzzy match explicitly below
    # with the injected deterministic gateway.
    shared_embed_text = "ERP RFP Austin ISD learning analytics platform."
    shared_vector = (await gw.embed([shared_embed_text])).vectors[0]

    s1 = await signals_services.promote_candidate_to_signal(
        session, _rfp(title="ERP RFP for Austin ISD", due_at="2026-09-01T17:00:00Z")
    )
    await session.flush()
    s1.vector_embedding = shared_vector
    await session.flush()
    await session.commit()

    # Second RFP: same entity, different due_at (exact key miss) but near-identical text.
    s2 = await signals_services.promote_candidate_to_signal(
        session, _rfp(title="ERP RFP — Austin ISD", due_at="2026-10-01T17:00:00Z")
    )
    assert s2.id != s1.id, "different due_at → exact dedupe misses, two rows exist"
    await session.flush()
    s2.vector_embedding = shared_vector
    await session.flush()
    await session.commit()

    # Drive the fuzzy path through the same standalone entry the store seam uses, but
    # via the public service re-export so we exercise the cross-module seam.
    from civicsignals_api.modules.signals.fuzzy_dedupe import run_fuzzy_dedupe

    refreshed_s2 = await session.get(Signal, s2.id)
    assert refreshed_s2 is not None
    fuzzy_result = await run_fuzzy_dedupe(
        session,
        candidate_signal=refreshed_s2,
        signal_type=SignalType.RFP_POSTED,
        new_doc_ids=[uuid.uuid4()],
        new_confidence=0.85,
        gateway=gw,
        config=cfg,
    )
    await session.commit()

    assert fuzzy_result.matched is True
    assert fuzzy_result.routed_to_review is True, "pre-graduation match must go to review"
    review_count = await session.scalar(
        select(func.count())
        .select_from(SignalFuzzyReview)
        .where(SignalFuzzyReview.status == REVIEW_STATUS_PENDING)
    )
    assert int(review_count or 0) >= 1


# ===========================================================================
# 6. Entity extraction / linking (E11) — link when matched, degrade when not
# ===========================================================================
#
# The mocked-service link/unresolved behaviour is covered in
# ``extraction/tests/test_entity_extraction.py`` (``test_entity_linking_resolves_known_entity``
# / ``test_entity_linking_flags_unknown_entity``). The gap is the *real-DB* linking
# through the pipeline: a seeded ``entities_entity`` is resolved (signal carries
# entity_id) vs. an unseeded mention degrades gracefully (entity_id NULL → review).


async def test_entity_links_to_existing_row_through_pipeline(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extracted org matching a seeded entity links the signal to it (E11, real DB)."""
    entity = await entities_services.upsert_entity(
        session,
        natural_key="nces_leaid",
        nces_leaid="4800077",
        type="school_district",
        name="Northshore School District",
        country="US",
        state="WA",
        enrollment=23400,
    )
    await session.flush()

    doc = _stored_doc(doc_id=uuid.uuid4(), recipe_id="wa_link_rfps")
    gw, _extract_be = _gateway(
        relevance=_relevant(0.9),
        entity=_entity_reply("Northshore School District"),  # exact name → links
        signal=_signal_candidates(
            [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.9,
                    "fields": {
                        "title": "Network upgrade RFP",
                        "summary": "RFP for a district network upgrade.",
                        "due_at": "2026-09-01T17:00:00Z",
                    },
                }
            ]
        ),
    )

    await _run_pipeline(session, gateway=gw, doc=doc, body=b"network rfp", monkeypatch=monkeypatch)
    await session.commit()

    sig = (
        (await session.execute(select(Signal).where(Signal.recipe_id == doc.recipe_id)))
        .scalars()
        .one()
    )
    assert sig.entity_id == entity.id
    assert sig.entity_name_raw == "Northshore School District"


async def test_entity_link_degrades_when_no_match_through_pipeline(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unseeded org mention degrades gracefully: entity_id NULL → held for review (E11)."""
    doc = _stored_doc(doc_id=uuid.uuid4(), recipe_id="wa_unlinked_rfps")
    gw, _extract_be = _gateway(
        relevance=_relevant(0.9),
        entity=_entity_reply("Totally Unknown Authority"),  # no seeded entity → unresolved
        signal=_signal_candidates(
            [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.9,
                    "fields": {
                        "title": "Unlinked RFP",
                        "summary": "RFP whose entity could not be resolved.",
                        "due_at": "2026-09-01T17:00:00Z",
                    },
                }
            ]
        ),
    )

    await _run_pipeline(session, gateway=gw, doc=doc, body=b"unlinked rfp", monkeypatch=monkeypatch)
    await session.commit()

    sig = (
        (await session.execute(select(Signal).where(Signal.recipe_id == doc.recipe_id)))
        .scalars()
        .one()
    )
    # Graceful degradation: no canonical entity → entity_id NULL, raw name preserved,
    # signal held for review (doc 19 §4.3) rather than lost.
    assert sig.entity_id is None
    assert sig.entity_name_raw == "Totally Unknown Authority"
    assert sig.review_required is True


# ===========================================================================
# 7. Routing invariant (F3) — disjoint ICPs over a pipeline-produced signal
# ===========================================================================
#
# ``signals/tests/test_workspace_score_db.py`` proves the fan-out + isolation with
# *hand-built* Signal rows. This is the service-layer complement to the Playwright e2e:
# the SAME assertion over a signal that was actually produced by the extraction
# pipeline, fanned out across THREE workspaces with disjoint ICPs.


async def test_routing_invariant_three_disjoint_workspaces(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pipeline-produced TX rfp routes to ONLY the TX/rfp workspace among three (F3)."""
    await entities_services.upsert_entity(
        session,
        natural_key="nces_leaid",
        nces_leaid="4800123",
        type="school_district",
        name="Bluebonnet ISD",
        country="US",
        state="TX",
        enrollment=18000,
    )
    await session.flush()

    # Three disjoint ICPs: only ws_match (TX + rfp_posted) overlaps the produced signal.
    ws_match = await _workspace(session, email="match.route@example.com")
    ws_wrong_state = await _workspace(session, email="state.route@example.com")
    ws_wrong_type = await _workspace(session, email="type.route@example.com")
    await _icp(session, workspace_id=ws_match, states=["TX"], signal_types=["rfp_posted"])
    await _icp(session, workspace_id=ws_wrong_state, states=["CA"], signal_types=["rfp_posted"])
    await _icp(session, workspace_id=ws_wrong_type, states=["TX"], signal_types=["grant_awarded"])
    await session.flush()

    doc = _stored_doc(doc_id=uuid.uuid4(), recipe_id="tx_route_rfps")
    gw, _extract_be = _gateway(
        relevance=_relevant(0.9),
        entity=_entity_reply("Bluebonnet ISD"),
        signal=_signal_candidates(
            [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.9,
                    "fields": {
                        "title": "Transportation RFP",
                        "summary": "RFP for student transportation services.",
                        "due_at": "2026-09-01T17:00:00Z",
                    },
                }
            ]
        ),
    )

    await _run_pipeline(
        session, gateway=gw, doc=doc, body=b"transport rfp", monkeypatch=monkeypatch
    )
    await session.flush()
    sig = (
        (await session.execute(select(Signal).where(Signal.recipe_id == doc.recipe_id)))
        .scalars()
        .one()
    )

    written = await signals_services.score_signal_for_all_workspaces(
        session, signal_id=sig.id, now=NOW
    )
    await session.commit()

    assert written == 1
    assert len(await _scores(session, ws_match)) == 1
    assert await _scores(session, ws_wrong_state) == [], "wrong-state ICP must not match"
    assert await _scores(session, ws_wrong_type) == [], "wrong-type ICP must not match"
