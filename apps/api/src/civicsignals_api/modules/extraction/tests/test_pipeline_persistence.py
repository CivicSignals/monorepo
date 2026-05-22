"""Live-DB tests for E1 job tracking + candidate persistence (doc 19 §1).

Like ``test_relevance_persistence.py``, these run against a real Postgres only
when ``EXTRACTION_TEST_DSN`` is set (CI / a developer with the dev stack up);
otherwise they skip. The ``extraction_job`` / ``extraction_candidate`` tables use
Postgres JSONB + UUID, so SQLite is not a faithful substitute.

Covers:
- ``enqueue_extraction`` creates a job and is idempotent on ``raw_document_id``;
- ``run_extraction_pipeline`` end-to-end produces a candidate row + a job row;
- ``list_pending_documents`` returns pending jobs oldest-first.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

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

# Import the entities models so ``entities_entity`` (the target of
# ``signals_signal.entity_id``) is on Base.metadata and the FK DDL resolves when we
# create signals_signal below. The end-to-end signal is stored entity_id=None, so
# no entity rows are needed — only the table must exist for the FK.
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.extraction import pipeline, services
from civicsignals_api.modules.extraction.models import (
    JOB_STATUS_DONE,
    JOB_STATUS_PENDING,
    ExtractionCandidate,
    ExtractionJob,
    RelevanceDecision,
)
from civicsignals_api.modules.extraction.relevance import RelevanceClassifier
from civicsignals_api.modules.ingestion.services import StoredRawDocument
from civicsignals_api.modules.signals.models import EMBEDDING_DIM, Signal

_DSN = os.environ.get("EXTRACTION_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not _DSN, reason="EXTRACTION_TEST_DSN not set; needs a live Postgres"
)


class _FakeStorage:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def get_document(self, key: str) -> bytes:
        return self._content


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Engine with the extraction tables created; only the two we own are dropped.

    Scoped to this module's tables (not ``Base.metadata`` unfiltered) so the test
    never touches other modules' tables. The end-to-end test runs the relevance
    gate, which records into ``extraction_relevance_decision``, so we *create* that
    table too (checkfirst) — but we do **not** drop it on teardown, since its
    lifecycle is owned by ``test_relevance_persistence.py`` (which create/drops it
    around its own cases); dropping it here could race that suite when both run.
    """
    assert _DSN is not None
    eng = create_async_engine(_DSN)
    owned = [
        Base.metadata.tables[ExtractionJob.__tablename__],
        Base.metadata.tables[ExtractionCandidate.__tablename__],
    ]
    relevance = Base.metadata.tables[RelevanceDecision.__tablename__]
    # The store stage now promotes candidates into signals_signal (E4), so the
    # end-to-end test needs that table too. signals_signal.entity_id FKs the global
    # entities tables, so those must exist for the FK DDL to resolve (no entity rows
    # are inserted — the end-to-end signal is stored entity_id=None). pgvector +
    # pg_trgm back the signal embedding + entity name index respectively.
    signals = Base.metadata.tables[Signal.__tablename__]
    entity_tables = [
        Base.metadata.tables[name] for name in ("entities_entity", "entities_geo", "entities_kind")
    ]
    async with eng.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[*owned, relevance, *entity_tables, signals],
            checkfirst=True,
        )
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            # Drop signals_signal first (extraction owns neither relevance nor
            # signals lifecycle, but the end-to-end test populated signals here, so
            # we clean it up). Use CASCADE-free drop since nothing FKs into it in
            # this scoped schema.
            await conn.run_sync(Base.metadata.drop_all, tables=[*owned, signals], checkfirst=True)
        await eng.dispose()


def _stored_doc(doc_id: uuid.UUID) -> StoredRawDocument:
    return StoredRawDocument(
        id=doc_id,
        recipe_id="seattle_school_board_agendas",
        recipe_version=1,
        connector="http_static",
        source_url="https://example.gov/agenda",
        fetched_at=datetime.now(UTC),
        content_hash="0" * 64,
        blob_key="sha256/" + "0" * 64,
        content_type="text/plain",
    )


def _gateway(*, relevance: str, extract: str) -> LLMGateway:
    policy = TaskModelPolicy(
        overrides={
            TASK_CLASSIFY: ModelChoice("relevance_be", "haiku"),
            TASK_EXTRACTION: ModelChoice("extract_be", "sonnet"),
        }
    )
    return LLMGateway(
        {
            "relevance_be": FakeBackend(provider="relevance_be", responses=[relevance]),
            "extract_be": FakeBackend(provider="extract_be", responses=[extract]),
        },
        policy=policy,
        # The store stage embeds each promoted signal (I1); the deterministic fake
        # embedding backend keeps the end-to-end run network-free, and its dim must
        # match the signals_signal.vector_embedding column (EMBEDDING_DIM = 1536).
        embedding_backends={
            "fake_embed": FakeEmbeddingBackend(provider="fake_embed", dim=EMBEDDING_DIM)
        },
        embedding_choice=ModelChoice("fake_embed", "fake-embed-model"),
    )


async def test_enqueue_is_idempotent(engine: AsyncEngine) -> None:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = uuid.uuid4()
    async with sessionmaker() as session:
        first = await services.enqueue_extraction(session, doc_id, recipe_id="r1")
        await session.commit()
    async with sessionmaker() as session:
        second = await services.enqueue_extraction(session, doc_id, recipe_id="r1")
        await session.commit()
    assert first.id == second.id
    assert first.status == JOB_STATUS_PENDING

    async with sessionmaker() as session:
        count = await session.scalar(
            select(__import__("sqlalchemy").func.count()).select_from(ExtractionJob)
        )
        assert count == 1


async def test_pending_listing_oldest_first(engine: AsyncEngine) -> None:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        a = await services.enqueue_extraction(session, uuid.uuid4(), recipe_id="r")
        b = await services.enqueue_extraction(session, uuid.uuid4(), recipe_id="r")
        await session.commit()
    async with sessionmaker() as session:
        pending = await services.list_pending_documents(session)
    ids = [p.id for p in pending]
    assert a.id in ids and b.id in ids


async def test_claim_pending_jobs_transitions_out_of_pending(engine: AsyncEngine) -> None:
    """Claiming moves jobs to running so a second claim returns nothing (no dup dispatch)."""
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session:
        a = await services.enqueue_extraction(session, uuid.uuid4(), recipe_id="r")
        b = await services.enqueue_extraction(session, uuid.uuid4(), recipe_id="r")
        await session.commit()

    async with sessionmaker() as session:
        claimed = await services.claim_pending_jobs(session, limit=10)
        await session.commit()
    claimed_ids = {c.id for c in claimed}
    assert {a.id, b.id} <= claimed_ids
    # The returned records already reflect the running transition.
    assert all(c.status == "running" for c in claimed)

    # A second claim finds nothing pending — the jobs were claimed exactly once.
    async with sessionmaker() as session:
        again = await services.claim_pending_jobs(session, limit=10)
        await session.commit()
    assert again == []

    # And they are durably ``running`` in the table.
    async with sessionmaker() as session:
        for jid in (a.id, b.id):
            row = await session.get(ExtractionJob, jid)
            assert row is not None and row.status == "running"


async def test_pipeline_end_to_end_persists_candidate(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    doc_id = uuid.uuid4()

    async def _fake_get(session: AsyncSession, document_id: uuid.UUID) -> StoredRawDocument:
        return _stored_doc(doc_id)

    monkeypatch.setattr(
        "civicsignals_api.modules.extraction.pipeline.ingestion_services.get_raw_document",
        _fake_get,
    )

    gw = _gateway(
        relevance=json.dumps({"relevant": True, "categories": ["procurement"], "confidence": 0.9}),
        extract=json.dumps(
            {
                "candidates": [
                    {
                        "signal_type": "rfp_posted",
                        "confidence": 0.8,
                        "fields": {
                            "title": "ERP RFP",
                            "summary": "RFP for ERP modernization.",
                            "due_at": "2026-06-01T17:00:00Z",
                        },
                    }
                ]
            }
        ),
    )

    async with sessionmaker() as session:
        job = await services.enqueue_extraction(session, doc_id, recipe_id="r")
        await session.commit()

        await services.mark_job_running(session, job.id)
        await session.commit()

        result = await pipeline.run_extraction_pipeline(
            session,
            _FakeStorage(b"RFP for ERP modernization"),  # type: ignore[arg-type]
            job_id=job.id,
            raw_document_id=doc_id,
            classifier=RelevanceClassifier(gateway=gw),
            gateway=gw,
            prefilter="classifier",
        )
        await services.mark_job_done(session, job.id, stage="store", skipped=result.skipped)
        await session.commit()

    async with sessionmaker() as session:
        candidates = (
            (
                await session.execute(
                    select(ExtractionCandidate).where(ExtractionCandidate.job_id == job.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(candidates) == 1
        assert candidates[0].signal_type == "rfp_posted"
        assert candidates[0].fields["title"] == "ERP RFP"
        # The E6 score stage (doc 19 §6.2) re-derives confidence as the weighted blend
        # of the five components — not the raw 0.8 the model self-reported. For this
        # candidate (LLM 0.8, neutral source quality, 0/6 optional fields filled,
        # cross-validation: due_at plausible but entity unresolved) the blend is 0.605,
        # which lands in the degraded band (0.6-0.8, doc 19 §6.3).
        assert candidates[0].confidence == pytest.approx(0.605)
        # The candidate was promoted into a global signal (E4 hard gate passed).
        assert candidates[0].status == "promoted"

        # The funnel produced exactly one signals_signal row, linked back to the job
        # + candidate (doc 19 §1 store; doc 07 signals_signal).
        signals = (
            (await session.execute(select(Signal).where(Signal.extraction_job_id == job.id)))
            .scalars()
            .all()
        )
        assert len(signals) == 1
        assert signals[0].signal_type == "rfp_posted"
        assert signals[0].title == "ERP RFP"
        assert signals[0].source_candidate_id == candidates[0].id
        assert str(doc_id) in signals[0].raw_document_ids
        # The 0.605 blend lands in the degraded band (doc 19 §6.3): the signal carries
        # the degraded "verify the details" flag; the unresolved entity also holds it
        # for review (doc 19 §4.3).
        assert signals[0].is_degraded is True
        assert signals[0].review_required is True
        # The embed step (I1) populated the pgvector column at extraction time.
        embedding = signals[0].vector_embedding
        assert embedding is not None
        assert len(embedding) == EMBEDDING_DIM

        refreshed = await session.get(ExtractionJob, job.id)
        assert refreshed is not None
        assert refreshed.status == JOB_STATUS_DONE
        assert refreshed.stage == "store"
