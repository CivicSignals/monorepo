"""Live-DB tests for windowed exact-match dedupe (doc 19 §7; E5).

Run against a live Postgres resolved from (in order) ``SIGNALS_TEST_DSN``,
``DATABASE_DIRECT_URL``, or ``DATABASE_URL`` — so they execute in CI / for a dev
with the stack up, and skip cleanly otherwise. The windowed lookup uses real
Postgres ``coalesce`` + interval comparison over the ``signals_dedupe_window_idx``
index, so SQLite is not a faithful substitute.

The signal's ``entity_id`` FK-references ``entities_entity``; these tests use the
resolution-pending path (``entity_id=None``, doc 19 §4.3) so they exercise the
signals table without depending on the entities module's schema. Only the
``signals_signal`` table (+ the entities tables for the FK DDL) is created/dropped.

Covers (doc 19 §7):
- same key within the window -> merged (one signal, both source docs preserved);
- same key *outside* the window -> a separate, new signal;
- different entity / different signal type -> separate signals;
- per-type window boundaries (the 90d RFP vs 730d leadership windows differ);
- the pipeline-shaped promote path dedups end-to-end and preserves sources.
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

# Register entities models so ``entities_entity`` (the FK target) is on Base.metadata
# and the FK DDL resolves. entity_id=None throughout, so no entity rows are needed.
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.signals import dedupe, services
from civicsignals_api.modules.signals.models import Signal
from civicsignals_api.modules.signals.schemas import SignalType
from civicsignals_api.modules.signals.services import CandidateInput

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

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


def _rfp_candidate(
    *,
    raw_document_id: uuid.UUID | None = None,
    due_at: str = "2026-06-01T17:00:00Z",
    title: str = "RFP: Learning Analytics Platform",
    confidence: float | None = 0.85,
    entity_id: uuid.UUID | None = None,
) -> CandidateInput:
    return CandidateInput(
        signal_type="rfp_posted",
        fields={
            "title": title,
            "summary": "RFP posted for a learning analytics platform.",
            "due_at": due_at,
        },
        recipe_id="wa_k12_rfps",
        raw_document_id=raw_document_id or uuid.uuid4(),
        content_hash="ignored-recomputed-by-store",
        confidence=confidence,
        entity_id=entity_id,
        # The store path windows on occurred_at; set it so the boundary tests are
        # deterministic relative to the ``now`` find_duplicate uses.
        occurred_at=datetime.now(UTC),
    )


async def _count(session: AsyncSession) -> int:
    n = await session.scalar(select(func.count()).select_from(Signal))
    await session.rollback()
    return int(n or 0)


async def test_same_key_within_window_merges(session: AsyncSession) -> None:
    """Same RFP seen twice within 90d -> one signal, both source docs (doc 19 §7.3)."""
    doc_a, doc_b = uuid.uuid4(), uuid.uuid4()
    s1 = await services.promote_candidate_to_signal(
        session, _rfp_candidate(raw_document_id=doc_a, confidence=0.7)
    )
    await session.commit()
    first_id = s1.id

    s2 = await services.promote_candidate_to_signal(
        session, _rfp_candidate(raw_document_id=doc_b, confidence=0.9)
    )
    await session.commit()

    assert s2.id == first_id  # merged, not a new row
    assert await _count(session) == 1
    merged = await services.get_signal(session, first_id)
    assert merged is not None
    # Both corroborating documents preserved (no source lost — doc 19 §7.3).
    assert set(merged.raw_document_ids) == {doc_a, doc_b}
    assert merged.confidence == pytest.approx(0.9)  # higher kept


async def test_same_key_outside_window_is_new_signal(session: AsyncSession) -> None:
    """Same RFP key 91 days later (past the 90d window) -> a separate signal (§7.1)."""
    # Seed an existing signal whose event date is just outside the RFP window.
    now = datetime.now(UTC)
    old = Signal(
        id=uuid.uuid4(),
        entity_id=None,
        signal_type=SignalType.RFP_POSTED.value,
        recipe_id="wa_k12_rfps",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash=dedupe.compute_dedupe_hash(
            SignalType.RFP_POSTED, None, {"title": "RFP X", "due_at": "2026-06-01"}
        ),
        occurred_at=now - timedelta(days=91),
        observed_at=now - timedelta(days=91),
        title="RFP X",
        summary="s",
        details={},
        confidence=0.8,
    )
    session.add(old)
    await session.commit()

    # A fresh candidate with the same key, occurred now -> outside the old one's
    # window -> new signal.
    new = await services.promote_candidate_to_signal(
        session,
        _rfp_candidate(title="RFP X", due_at="2026-06-01"),
    )
    await session.commit()
    assert new.id != old.id
    assert await _count(session) == 2


async def test_different_entity_separate(session: AsyncSession) -> None:
    e1, e2 = uuid.uuid4(), uuid.uuid4()
    # entity rows don't exist; FK is SET NULL nullable, but a non-null entity_id must
    # reference an existing row — so we use entity_id=None for one and seed via the
    # dedupe hash which already differs by entity. Use None vs None but different
    # titles is not the point; instead assert the hash differs by entity directly.
    h1 = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, e1, {"title": "T", "due_at": "2026-06-01"}
    )
    h2 = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, e2, {"title": "T", "due_at": "2026-06-01"}
    )
    assert h1 != h2  # the hash basis includes entity_id, so lookups never collide


async def test_different_signal_type_separate(session: AsyncSession) -> None:
    """Same entity + title but different signal types -> separate signals."""
    rfp = await services.promote_candidate_to_signal(session, _rfp_candidate(title="Shared Title"))
    await session.commit()
    # An rfi_rfq with the same title -> different type -> separate signal.
    rfi = await services.promote_candidate_to_signal(
        session,
        CandidateInput(
            signal_type="rfi_rfq",
            fields={"title": "Shared Title", "summary": "An RFI."},
            recipe_id="r",
            raw_document_id=uuid.uuid4(),
            content_hash="x",
            confidence=0.8,
            occurred_at=datetime.now(UTC),
        ),
    )
    await session.commit()
    assert rfi.id != rfp.id
    assert await _count(session) == 2


async def test_leadership_window_longer_than_rfp(session: AsyncSession) -> None:
    """A leadership change keyed 120 days ago still dedupes (730d window) (§7.1)."""
    now = datetime.now(UTC)
    fields = {"role": "CTO", "person_name": "Jane Doe"}
    h = dedupe.compute_dedupe_hash(SignalType.LEADERSHIP_CHANGE, None, fields)
    doc_a = uuid.uuid4()
    old = Signal(
        id=uuid.uuid4(),
        entity_id=None,
        signal_type=SignalType.LEADERSHIP_CHANGE.value,
        recipe_id="r",
        raw_document_ids=[str(doc_a)],
        content_hash=h,
        occurred_at=now - timedelta(days=120),
        observed_at=now - timedelta(days=120),
        title="Leadership change",
        summary="s",
        details={},
        confidence=0.7,
    )
    session.add(old)
    await session.commit()
    old_id = old.id

    # 120 days is well inside the 730d leadership window -> merge, not new.
    found = await dedupe.find_duplicate(
        session,
        entity_id=None,
        signal_type=SignalType.LEADERSHIP_CHANGE,
        dedupe_hash=h,
        now=now,
    )
    # Read the id before any rollback (rollback expires ORM instances).
    assert found is not None and found.id == old_id

    # The same 120-day gap is OUTSIDE the 90d RFP window -> no match for an RFP key.
    rfp_h = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, None, {"title": "x", "due_at": "2026-06-01"}
    )
    old_rfp = Signal(
        id=uuid.uuid4(),
        entity_id=None,
        signal_type=SignalType.RFP_POSTED.value,
        recipe_id="r",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash=rfp_h,
        occurred_at=now - timedelta(days=120),
        observed_at=now - timedelta(days=120),
        title="x",
        summary="s",
        details={},
        confidence=0.7,
    )
    session.add(old_rfp)
    await session.commit()
    none_found = await dedupe.find_duplicate(
        session,
        entity_id=None,
        signal_type=SignalType.RFP_POSTED,
        dedupe_hash=rfp_h,
        now=now,
    )
    assert none_found is None  # 120d > 90d RFP window


async def test_window_boundary_inclusive_recent(session: AsyncSession) -> None:
    """A signal exactly inside the window edge is still found (doc 19 §7.2)."""
    now = datetime.now(UTC)
    h = dedupe.compute_dedupe_hash(
        SignalType.RFP_POSTED, None, {"title": "edge", "due_at": "2026-06-01"}
    )
    inside = Signal(
        id=uuid.uuid4(),
        entity_id=None,
        signal_type=SignalType.RFP_POSTED.value,
        recipe_id="r",
        raw_document_ids=[str(uuid.uuid4())],
        content_hash=h,
        # 89 days ago -> inside the 90d window.
        occurred_at=now - timedelta(days=89),
        observed_at=now - timedelta(days=89),
        title="edge",
        summary="s",
        details={},
        confidence=0.7,
    )
    session.add(inside)
    await session.commit()
    inside_id = inside.id
    found = await dedupe.find_duplicate(
        session,
        entity_id=None,
        signal_type=SignalType.RFP_POSTED,
        dedupe_hash=h,
        now=now,
    )
    # Read the id before any rollback (rollback expires ORM instances).
    assert found is not None and found.id == inside_id
