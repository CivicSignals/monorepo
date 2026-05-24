# SPDX-License-Identifier: AGPL-3.0-only
"""End-to-end regression guard for the pipeline-wiring fixes (crawl → feed).

This drives the **real** chain, with no shortcuts, against a real Postgres
(resolved from ``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` / ``SIGNALS_TEST_DSN``)
and a deterministic ``FakeBackend`` gateway + moto-backed S3, to prove the two gaps
this branch closes are actually wired:

1. **Crawl persists raw documents (Fix #1).** The crawl path runs the connector
   lifecycle with an *injected* static fetcher (no network) through the production
   seam ``ingestion.services.crawl_recipe_with_connector_collecting_raw_documents``,
   then persists each fetched document via ``store_crawled_raw_documents`` →
   ``store_raw_document``. We assert an ``ingestion_raw_document`` row now exists
   (previously the crawl loop discarded the bytes).

2. **New signals get scored per workspace via the real trigger (Fix #2).** The real
   extraction orchestrator (``run_extraction_pipeline``) promotes a candidate into a
   ``signals_signal`` row and threads the new signal ids out. We then drive scoring
   **the way ``worker_extract`` would**: by running the body of the
   ``signals.score_signal`` Celery task (``signals.tasks._score_signal_async``) — NOT
   a direct ``score_signal_for_all_workspaces`` call — and assert a
   ``signals_workspace_score`` row is written for the matching workspace, that the
   feed (``list_workspace_signals``) returns the signal, and that a disjoint workspace
   gets no score row.

The full ``raw_document → signal → workspace_score → feed`` chain, end to end, with
scoring via the wiring under test rather than a manual fan-out call.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import boto3
import pytest
import pytest_asyncio
from moto import mock_aws
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api import db as db_module
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
from civicsignals_api.modules.accounts.models import (  # noqa: F401
    Membership,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.entities import services as entities_services
from civicsignals_api.modules.extraction import pipeline
from civicsignals_api.modules.extraction.relevance import RelevanceClassifier
from civicsignals_api.modules.icp.models import IcpDefinition
from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.models import RawDocument
from civicsignals_api.modules.ingestion.storage import RawDocumentStorage
from civicsignals_api.modules.signals import services as signals_services
from civicsignals_api.modules.signals import tasks as signals_tasks
from civicsignals_api.modules.signals.models import EMBEDDING_DIM, Signal
from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore

_DSN = (
    os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
    or os.environ.get("SIGNALS_TEST_DSN")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

_BUCKET = "civic-raw-e2e"
# The committed reference recipe (recipes/wa-state-webs/recipe.yml): http_static
# connector, prefilter=assume_relevant, required field `title` via
# `h1.solicitation-title`, signal_types [rfp_posted, contract_award].
_RECIPE_ID = "wa-state-webs"
_SOURCE_URL = "https://webs.des.wa.gov/rfp/erp-2026"
# HTML whose title selector matches the recipe's primary `h1.solicitation-title`.
_HTML = (
    "<html><body>"
    '<h1 class="solicitation-title">ERP modernization RFP</h1>'
    "<p>Northshore School District seeks an ERP platform. Due 2026-09-01.</p>"
    "</body></html>"
)


# ---------------------------------------------------------------------------
# Static, network-free fetcher injected into the crawl path (Fix #1)
# ---------------------------------------------------------------------------


class _StaticFetcher:
    """A runner ``Fetcher`` that serves fixed HTML for any URL — no network."""

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:
        return 200, _HTML, {"content-type": "text/html; charset=utf-8"}

    def robots_txt(self, url: str, *, user_agent: str) -> str | None:
        return None


# ---------------------------------------------------------------------------
# Fixtures: full-schema DB + moto S3 storage shared across crawl + extraction
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A clean full-schema session (the chain spans many modules' tables)."""
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
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await engine.dispose()


@pytest.fixture
def storage() -> Iterator[RawDocumentStorage]:
    """A moto-backed content-addressable raw-doc store (no real S3)."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield RawDocumentStorage(client, _BUCKET)


# ---------------------------------------------------------------------------
# Deterministic LLM gateway (entity Pass-1 reply, then the signal candidate)
# ---------------------------------------------------------------------------


def _entity_reply(org_name: str) -> str:
    return json.dumps(
        {
            "organizations": [{"name": org_name, "confidence": 0.9}],
            "persons": [{"name": "Jane Smith", "role": "CTO", "confidence": 0.85}],
            "monetary_amounts": [{"amount_cents": 40000000, "currency": "USD", "confidence": 0.9}],
            "dates": [{"value": "2026-09-01", "kind": "due_date", "confidence": 0.88}],
            "products_categories": ["ERP"],
            "vendors_mentioned": [],
            "contract_terms_mentions": [],
            "raw_keywords": ["procurement"],
            "extraction_confidence": 0.85,
            "extraction_warnings": [],
        }
    )


def _signal_reply() -> str:
    return json.dumps(
        {
            "candidates": [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.9,
                    "fields": {
                        "title": "ERP modernization RFP",
                        "summary": "RFP for an ERP modernization platform.",
                        "due_at": "2026-09-01T17:00:00Z",
                        "rfp_number": "RFP-2026-ERP",
                        "amount_cents": 40000000,
                        "posting_agency": "Northshore SD",
                        "contact_name": "Jane Smith",
                        "submission_url": "https://webs.des.wa.gov/submit",
                        "requirements": ["SIS integration"],
                    },
                }
            ]
        }
    )


def _gateway() -> LLMGateway:
    """Network-free gateway: entity Pass-1 then signal reply on the extract task."""
    policy = TaskModelPolicy(
        overrides={
            TASK_CLASSIFY: ModelChoice("relevance_be", "haiku"),
            TASK_EXTRACTION: ModelChoice("extract_be", "sonnet"),
        }
    )
    return LLMGateway(
        {
            # assume_relevant short-circuits the gate, but seed a reply defensively.
            "relevance_be": FakeBackend(
                provider="relevance_be",
                responses=[json.dumps({"relevant": True, "confidence": 0.9})],
            ),
            "extract_be": FakeBackend(
                provider="extract_be",
                responses=[_entity_reply("Northshore School District"), _signal_reply()],
            ),
        },
        policy=policy,
        embedding_backends={
            "fake_embed": FakeEmbeddingBackend(provider="fake_embed", dim=EMBEDDING_DIM)
        },
        embedding_choice=ModelChoice("fake_embed", "fake-embed-model"),
    )


# ---------------------------------------------------------------------------
# Workspace / ICP builders (reuse the same shapes as test_feed_route.py)
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
        keywords_required=[],
        keywords_excluded=[],
        threshold=threshold,
        is_active=True,
    )
    session.add(icp)
    await session.flush()
    return icp


# ===========================================================================
# The chain: crawl → raw_document → signal → workspace_score → feed
# ===========================================================================


async def test_full_pipeline_chain_crawl_to_feed(
    session: AsyncSession, storage: RawDocumentStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove the real chain end to end, with scoring via the wiring (not a direct call)."""
    # --- Seed the canonical entity + two disjoint-ICP workspaces -----------------
    entity = await entities_services.upsert_entity(
        session,
        natural_key="nces_leaid",
        nces_leaid="5302130",
        type="school_district",
        name="Northshore School District",
        country="US",
        state="WA",
        enrollment=23400,
    )
    await session.flush()

    ws_match = await _workspace(session, email="match.e2e@example.com")
    ws_other = await _workspace(session, email="other.e2e@example.com")
    # Matching ICP: WA + rfp_posted overlaps the produced signal.
    await _icp(session, workspace_id=ws_match, states=["WA"], signal_types=["rfp_posted"])
    # Disjoint ICP: CA + news_mention overlaps nothing the signal carries.
    await _icp(session, workspace_id=ws_other, states=["CA"], signal_types=["news_mention"])
    await session.commit()

    # === Fix #1: crawl with an injected static fetcher, then persist raw docs =====
    records, raw_docs = ingestion_services.crawl_recipe_with_connector_collecting_raw_documents(
        _RECIPE_ID, [_SOURCE_URL], fetcher=_StaticFetcher()
    )
    assert len(records) == 1, "the crawl must normalize one canonical record"
    assert len(raw_docs) == 1, "the crawl must surface the fetched raw document"

    stored = await ingestion_services.store_crawled_raw_documents(session, storage, raw_docs)
    await session.commit()
    assert len(stored) == 1
    raw_document_id = stored[0].id

    # Assert the row now exists (the gap Fix #1 closes).
    raw_count = await session.scalar(select(func.count()).select_from(RawDocument))
    assert int(raw_count or 0) == 1, "ingestion_raw_document must be populated by the crawl"
    fetched = await ingestion_services.get_raw_document(session, raw_document_id)
    assert fetched is not None
    assert fetched.recipe_id == _RECIPE_ID

    # === Real extraction over the stored snapshot (no shortcut) ===================
    # Resolve the prefilter exactly as the production task does (recipe-driven).
    recipe = signals_services_recipe_prefilter()
    gateway = _gateway()
    result = await pipeline.run_extraction_pipeline(
        session,
        storage,
        job_id=uuid.uuid4(),
        raw_document_id=raw_document_id,
        classifier=RelevanceClassifier(gateway=gateway),
        gateway=gateway,
        prefilter=recipe,
    )
    await session.commit()

    assert result.skipped is False
    assert result.relevant is True
    assert len(result.signal_ids) == 1, "extraction must promote exactly one signal"

    sig_count = await session.scalar(select(func.count()).select_from(Signal))
    assert int(sig_count or 0) == 1, "signals_signal must be populated by extraction"
    signal = (await session.execute(select(Signal))).scalars().one()
    assert signal.signal_type == "rfp_posted"
    assert signal.entity_id == entity.id  # E11 linked to the seeded entity
    signal_id = signal.id

    # === Fix #2: drive scoring via the REAL trigger (the score-worker task body) ==
    # NOT a direct score_signal_for_all_workspaces call — this is exactly what the
    # extraction task enqueues onto the `score` queue (signals.score_signal).
    #
    # Run the **real** Celery task body (``signals.score_signal``), not a hand-written
    # shortcut. That body wraps ``_score_signal_async`` in ``asyncio.run`` — exactly
    # how ``worker_score`` executes it. ``asyncio.run`` can't run inside pytest-asyncio's
    # already-running loop, and the task opens its session via the app's *global*
    # ``SessionLocal``/engine (db.py), which would otherwise get pinned to whatever loop
    # first touched it and then poison the next DB-backed test ("got Future attached to a
    # different loop" → "Event loop is closed"). So drive it on a clean, dedicated loop in
    # a worker thread (``asyncio.to_thread`` → fresh ``asyncio.run`` there), then dispose
    # the global engine so no pooled connection bound to that throwaway loop survives.
    # Our own assertions below keep using this test's per-fixture ``session`` engine.
    assert result.signal_ids == [signal_id]
    written = await asyncio.to_thread(signals_tasks.score_signal, str(signal_id))
    assert written == 1, "scoring via the wiring must write one (matching) workspace row"

    # Tear down the global engine the task body opened on its throwaway loop, so the
    # next DB-backed test's fixture is unaffected (no cross-loop pool reuse).
    await db_module.engine.dispose()

    # === Assert the workspace_score row + feed visibility =========================
    match_scores = (
        (
            await session.execute(
                select(WorkspaceScore).where(WorkspaceScore.workspace_id == ws_match)
            )
        )
        .scalars()
        .all()
    )
    assert len(match_scores) == 1
    assert match_scores[0].signal_id == signal_id

    # The disjoint workspace gets NO score row.
    other_scores = (
        (
            await session.execute(
                select(WorkspaceScore).where(WorkspaceScore.workspace_id == ws_other)
            )
        )
        .scalars()
        .all()
    )
    assert other_scores == [], "the disjoint-ICP workspace must not be scored"

    # The feed sees the signal for the matching workspace (proves the chain end-to-end).
    feed = await signals_services.list_workspace_signals(session, workspace_id=ws_match)
    assert len(feed.items) == 1
    assert feed.items[0].signal.id == signal_id

    # ...and is empty for the disjoint workspace.
    other_feed = await signals_services.list_workspace_signals(session, workspace_id=ws_other)
    assert other_feed.items == []


def signals_services_recipe_prefilter() -> str:
    """Resolve the wa-state-webs recipe's prefilter (assume_relevant) like the task does."""
    from civicsignals_api.modules.recipes import services as recipes_services

    return recipes_services.load_recipe(_RECIPE_ID).prefilter
