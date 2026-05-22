"""Decision-record persistence for the relevance gate (doc 19 §3.4, E8).

Mirrors A2's seed-test pattern: the persistence assertions run against a live
Postgres only when ``EXTRACTION_TEST_DSN`` is provided (CI / a developer with the
dev stack up); otherwise they skip so the suite stays green without a database.
The table uses Postgres JSONB, so SQLite is not a faithful substitute — we test
against the real dialect or not at all.
"""

from __future__ import annotations

import json
import os

import pytest

from civicsignals_api.llm_gateway import (
    TASK_CLASSIFY,
    FakeBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.extraction.models import RelevanceDecision
from civicsignals_api.modules.extraction.relevance import RelevanceClassifier
from civicsignals_api.modules.extraction.schemas import DocumentRef

_DSN = os.environ.get("EXTRACTION_TEST_DSN")
pytestmark = pytest.mark.skipif(
    not _DSN, reason="EXTRACTION_TEST_DSN not set; needs a live Postgres"
)


def _classifier(*responses: str) -> RelevanceClassifier:
    backend = FakeBackend(responses=list(responses))
    policy = TaskModelPolicy(overrides={TASK_CLASSIFY: ModelChoice("fake", "haiku")})
    gw = LLMGateway({"fake": backend}, policy=policy)
    return RelevanceClassifier(gateway=gw)


async def test_decision_is_persisted() -> None:
    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from civicsignals_api.db import Base

    assert _DSN is not None
    engine = create_async_engine(_DSN)
    try:
        # Create just this module's table (and any siblings on the metadata).
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        classifier = _classifier(
            json.dumps({"relevant": True, "categories": ["procurement"], "confidence": 0.9})
        )
        doc = DocumentRef(
            raw_document_id="rd-persist-1",
            recipe_id="seattle_school_board_agendas",
            source="Seattle School Board",
            text="RFP for ERP modernization",
        )

        async with sessionmaker() as session:
            verdict = await classifier.classify(doc, session=session)
            await session.commit()

        async with sessionmaker() as session:
            rows = (
                (
                    await session.execute(
                        select(RelevanceDecision).where(
                            RelevanceDecision.raw_document_id == "rd-persist-1"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            row = rows[0]
            assert row.recipe_id == "seattle_school_board_agendas"
            assert row.relevant is True
            assert row.confidence == pytest.approx(0.9)
            assert row.categories == ["procurement"]
            assert row.method == "classifier"
            assert row.model == "haiku"
            assert row.created_at is not None
            # The persisted row matches the returned verdict.
            assert row.relevant == verdict.relevant

            count = await session.scalar(select(func.count()).select_from(RelevanceDecision))
            assert count is not None and count >= 1
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


async def test_assume_relevant_decision_is_persisted_without_llm() -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from civicsignals_api.db import Base

    assert _DSN is not None
    engine = create_async_engine(_DSN)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        classifier = _classifier()  # no scripted responses -> any LLM call would echo
        doc = DocumentRef(
            raw_document_id="rd-persist-2",
            recipe_id="wa_state_webs_rfp_listings",
            text="a list of RFPs",
        )
        async with sessionmaker() as session:
            await classifier.classify(doc, prefilter="assume_relevant", session=session)
            await session.commit()
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    select(RelevanceDecision).where(
                        RelevanceDecision.raw_document_id == "rd-persist-2"
                    )
                )
            ).scalar_one()
            assert row.method == "assume_relevant"
            assert row.relevant is True
            assert row.model is None
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
