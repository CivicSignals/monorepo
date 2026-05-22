"""Tests for the signal embedding service (I1, doc 19 §4/§7.4, doc 14 §6.2).

Two layers:

- pure-unit tests of :func:`build_embedding_text` (no DB, no gateway) — the text the
  vector is computed from;
- live-DB tests of :func:`embed_signals` / :func:`backfill_embeddings` (the embed
  step + the backfill sweep), the dim-mismatch + failure-is-non-fatal paths, and an
  ANN nearest-neighbour smoke test against the ivfflat index.

The DB layer runs against a live Postgres resolved from ``SIGNALS_TEST_DSN`` /
``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` (so it executes in CI and for any dev
with the stack up) and skips cleanly otherwise — the ``signals_signal`` table uses
pgvector + JSONB + UUID, so SQLite is not a faithful substitute.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import Connection, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from civicsignals_api.db import Base
from civicsignals_api.llm_gateway import FakeEmbeddingBackend, LLMGateway, ModelChoice

# Import the entities models so ``entities_entity`` (the FK target of
# ``signals_signal.entity_id``) is registered on Base.metadata for create_all.
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.signals import services
from civicsignals_api.modules.signals.embedding import (
    backfill_embeddings,
    build_embedding_text,
    embed_signals,
    embedding_text_for_signal,
)
from civicsignals_api.modules.signals.models import EMBEDDING_DIM, Signal
from civicsignals_api.modules.signals.services import CandidateInput

# ---------------------------------------------------------------------------
# Pure-unit: embedding-text construction (no DB)
# ---------------------------------------------------------------------------


def test_build_embedding_text_title_and_summary() -> None:
    text_out = build_embedding_text(title="ERP RFP", summary="RFP for ERP modernization.")
    assert text_out == "ERP RFP RFP for ERP modernization."


def test_build_embedding_text_appends_key_fields() -> None:
    text_out = build_embedding_text(
        title="Contract expiring",
        summary="Vendor contract ending soon.",
        details={
            "vendor_name": "Acme Corp",
            "category": "cybersecurity",
            # An ignored key is not appended.
            "contract_number": "C-123",
        },
    )
    assert "Acme Corp" in text_out
    assert "cybersecurity" in text_out
    # contract_number is not in the curated embed-field set.
    assert "C-123" not in text_out


def test_build_embedding_text_joins_list_fields() -> None:
    text_out = build_embedding_text(
        title="t",
        summary="s",
        details={"raw_keywords": ["ai", "analytics", "k12"]},
    )
    assert "ai analytics k12" in text_out


def test_build_embedding_text_is_deterministic() -> None:
    kwargs = {"title": "x", "summary": "y", "details": {"vendor_name": "V"}}
    assert build_embedding_text(**kwargs) == build_embedding_text(**kwargs)  # type: ignore[arg-type]


def test_build_embedding_text_truncates() -> None:
    long_summary = "word " * 5000
    text_out = build_embedding_text(title="t", summary=long_summary)
    assert len(text_out) <= 8_000


def test_embedding_text_for_signal_uses_row_fields() -> None:
    sig = Signal(
        signal_type="rfp_posted",
        recipe_id="r",
        content_hash="h",
        title="ERP RFP",
        summary="Modernize ERP.",
        details={"vendor_name": "Acme"},
    )
    out = embedding_text_for_signal(sig)
    assert out.startswith("ERP RFP Modernize ERP.")
    assert "Acme" in out


# ---------------------------------------------------------------------------
# Live-DB: embed step + backfill + resilience
# ---------------------------------------------------------------------------

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
db = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

_TABLE = "signals_signal"
_ENTITY_TABLES = ("entities_entity", "entities_geo", "entities_kind")


def _drop_cascade(conn: Connection) -> None:
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")
    for tbl in _ENTITY_TABLES:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl} CASCADE")


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN)
    create_tables = [Base.metadata.tables[name] for name in _ENTITY_TABLES] + [
        Base.metadata.tables[_TABLE]
    ]
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


def _embed_gateway(*, dim: int = EMBEDDING_DIM, fail_times: int = 0) -> LLMGateway:
    backend = FakeEmbeddingBackend(provider="embed_be", dim=dim, fail_times=fail_times)
    return LLMGateway(
        {},
        embedding_backends={"embed_be": backend},
        embedding_choice=ModelChoice("embed_be", "fake-embed-model"),
        # max_attempts=1 so a fail_times>0 backend exhausts immediately (the embed
        # service then swallows the resulting error — best-effort).
        max_attempts=1,
    )


def _candidate(*, content_hash: str, title: str = "ERP RFP") -> CandidateInput:
    return CandidateInput(
        signal_type="rfp_posted",
        fields={
            "title": title,
            "summary": "RFP for ERP modernization.",
            "due_at": "2026-06-01T17:00:00Z",
            "posting_agency": "City IT Department",
        },
        recipe_id="r",
        raw_document_id=uuid.uuid4(),
        content_hash=content_hash,
    )


@db
async def test_embed_signals_populates_column(session: AsyncSession) -> None:
    sig = await services.promote_candidate_to_signal(session, _candidate(content_hash="k1"))
    await session.flush()
    assert sig.vector_embedding is None

    count = await embed_signals(session, [sig.id], gateway=_embed_gateway())
    await session.commit()

    assert count == 1
    stored = await session.get(Signal, sig.id)
    assert stored is not None
    assert stored.vector_embedding is not None
    assert len(stored.vector_embedding) == EMBEDDING_DIM


@db
async def test_embed_signals_skips_already_embedded(session: AsyncSession) -> None:
    sig = await services.promote_candidate_to_signal(session, _candidate(content_hash="k2"))
    await session.flush()
    assert await embed_signals(session, [sig.id], gateway=_embed_gateway()) == 1
    # A second pass re-embeds nothing (the column is already populated).
    assert await embed_signals(session, [sig.id], gateway=_embed_gateway()) == 0
    await session.rollback()


@db
async def test_embed_failure_is_non_fatal(session: AsyncSession) -> None:
    sig = await services.promote_candidate_to_signal(session, _candidate(content_hash="k3"))
    await session.flush()
    # The backend always fails; the service swallows it and leaves the column NULL —
    # the signal is never lost (doc 19 §12.1).
    count = await embed_signals(session, [sig.id], gateway=_embed_gateway(fail_times=5))
    await session.commit()
    assert count == 0
    stored = await session.get(Signal, sig.id)
    assert stored is not None
    assert stored.vector_embedding is None


@db
async def test_embed_dim_mismatch_leaves_column_null(session: AsyncSession) -> None:
    sig = await services.promote_candidate_to_signal(session, _candidate(content_hash="k4"))
    await session.flush()
    # A model whose dim != the column width is refused (not silently truncated).
    count = await embed_signals(session, [sig.id], gateway=_embed_gateway(dim=8))
    await session.commit()
    assert count == 0
    stored = await session.get(Signal, sig.id)
    assert stored is not None
    assert stored.vector_embedding is None


@db
async def test_backfill_embeddings_fills_null_rows(session: AsyncSession) -> None:
    ids = []
    for i in range(5):
        sig = await services.promote_candidate_to_signal(session, _candidate(content_hash=f"b{i}"))
        ids.append(sig.id)
    await session.commit()

    total = await backfill_embeddings(session, gateway=_embed_gateway(), batch_size=2)
    await session.commit()
    assert total == 5

    rows = (await session.execute(select(Signal).where(Signal.id.in_(ids)))).scalars().all()
    assert all(r.vector_embedding is not None for r in rows)


@db
async def test_backfill_respects_limit(session: AsyncSession) -> None:
    for i in range(4):
        await services.promote_candidate_to_signal(session, _candidate(content_hash=f"l{i}"))
    await session.commit()

    total = await backfill_embeddings(session, gateway=_embed_gateway(), batch_size=10, limit=2)
    await session.commit()
    assert total == 2
    remaining = await session.scalar(
        select(text("count(*)")).select_from(Signal).where(Signal.vector_embedding.is_(None))
    )
    assert int(remaining or 0) == 2


@db
async def test_ann_query_finds_nearest_neighbour(session: AsyncSession) -> None:
    """Smoke: an ANN cosine query returns the most-similar signal first.

    Embeds two distinct signals, then queries with one's own embedding text — the
    deterministic fake embedding makes identical text → identical vector, so that
    signal must be the nearest neighbour (cosine distance ~0).
    """
    target = await services.promote_candidate_to_signal(
        session, _candidate(content_hash="ann1", title="Cybersecurity RFP")
    )
    other = await services.promote_candidate_to_signal(
        session, _candidate(content_hash="ann2", title="Cafeteria furniture RFP")
    )
    await session.flush()
    gw = _embed_gateway()
    await embed_signals(session, [target.id, other.id], gateway=gw)
    await session.commit()

    query_text = embedding_text_for_signal(await session.get(Signal, target.id))  # type: ignore[arg-type]
    query_vec = (await gw.embed([query_text])).vectors[0]

    nearest = (
        await session.execute(
            select(Signal.id).order_by(Signal.vector_embedding.cosine_distance(query_vec)).limit(1)
        )
    ).scalar_one()
    assert nearest == target.id
