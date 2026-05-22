# SPDX-License-Identifier: AGPL-3.0-only
"""DB-backed tests for the F3 matcher → scorer → sparse-score pipeline (doc 14 §6).

Exercise the parts that need Postgres: the SQL candidate pre-filter, the sparse
``signals_workspace_score`` upsert (only relevant signals get a row, doc 14 §5.2),
workspace isolation, the ``signal.created`` trigger fan-out, and the ranked +
cursor-paginated feed read (``list_workspace_signals``, G1).

Run against a live Postgres resolved from ``SIGNALS_TEST_DSN`` / ``DATABASE_DIRECT_URL``
/ ``DATABASE_URL``; skips cleanly when none is set (the score table uses JSONB +
TEXT[] + Numeric + a pgvector FK target, so SQLite is not a faithful substitute). The
full ``Base.metadata`` is created so the ICP / workspace / entity FK targets exist.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base

# Import every module's models so Base.metadata is complete for create_all (the
# score table FK-references accounts_workspace + signals_signal; the matcher reads
# icp_definition + entities_entity).
from civicsignals_api.modules.accounts.models import (  # noqa: F401
    Membership,  # registers accounts_member on Base.metadata
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.entities.models import Entity
from civicsignals_api.modules.icp.models import IcpDefinition
from civicsignals_api.modules.signals import services
from civicsignals_api.modules.signals.models import Signal
from civicsignals_api.modules.signals.workspace_score_model import (
    SCORE_STATUS_DISMISSED,
    WorkspaceScore,
)

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# --- Builders ---------------------------------------------------------------


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
    states: list[str] | None = None,
    signal_types: list[str] | None = None,
    entity_kinds: list[str] | None = None,
    keywords_required: list[str] | None = None,
    countries: list[str] | None = None,
    threshold: int = 30,
) -> IcpDefinition:
    icp = IcpDefinition(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Test ICP",
        countries=countries if countries is not None else ["US"],
        states=states if states is not None else ["WA"],
        entity_kinds=entity_kinds if entity_kinds is not None else [],
        signal_types=signal_types if signal_types is not None else ["rfp_posted"],
        signal_weights={"rfp_posted": 1.0},
        keywords_required=keywords_required or [],
        keywords_excluded=[],
        threshold=threshold,
        is_active=True,
    )
    session.add(icp)
    await session.flush()
    return icp


async def _entity(
    session: AsyncSession,
    *,
    state: str = "WA",
    entity_type: str = "school_district",
    enrollment: int | None = 23400,
) -> Entity:
    ent = Entity(
        id=uuid.uuid4(),
        type=entity_type,
        name="Northshore SD",
        country="US",
        state=state,
        enrollment=enrollment,
    )
    session.add(ent)
    await session.flush()
    return ent


async def _signal(
    session: AsyncSession,
    *,
    signal_type: str = "rfp_posted",
    title: str = "RFP: productivity software",
    summary: str = "RFP for productivity software for the district.",
    confidence: float | None = 0.9,
    observed_at: datetime | None = None,
    entity: Entity | None = None,
) -> Signal:
    sig = Signal(
        id=uuid.uuid4(),
        entity_id=entity.id if entity is not None else None,
        signal_type=signal_type,
        recipe_id="r",
        raw_document_ids=[],
        content_hash=uuid.uuid4().hex,
        observed_at=observed_at or NOW,
        title=title,
        summary=summary,
        details={},
        confidence=confidence,
    )
    session.add(sig)
    await session.flush()
    return sig


async def _scores(session: AsyncSession, workspace_id: uuid.UUID) -> list[WorkspaceScore]:
    rows = await session.execute(
        select(WorkspaceScore).where(WorkspaceScore.workspace_id == workspace_id)
    )
    return list(rows.scalars().all())


# --- Sparse insert + threshold (doc 14 §5.2, §6.2) --------------------------


async def test_relevant_signal_gets_a_score_row(session: AsyncSession) -> None:
    ws = await _workspace(session, email="a@example.com")
    icp = await _icp(session, workspace_id=ws, keywords_required=["productivity"])
    ent = await _entity(session, state="WA")
    sig = await _signal(session, entity=ent)

    result = await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws, icp=icp, now=NOW
    )
    await session.commit()

    assert result is not None
    assert result.passes_threshold
    rows = await _scores(session, ws)
    assert len(rows) == 1
    assert rows[0].signal_id == sig.id
    assert float(rows[0].score) == pytest.approx(result.score)
    assert rows[0].score_breakdown["components"]  # breakdown persisted for F4
    # The matched flags are surfaced for F4's explanation (doc 14 §6.2).
    assert rows[0].matched_signal_type is True
    assert rows[0].matched_state is True


async def test_below_threshold_writes_no_row(session: AsyncSession) -> None:
    ws = await _workspace(session, email="b@example.com")
    # An unreachable threshold → matcher passes but the score never clears it.
    icp = await _icp(session, workspace_id=ws, threshold=100)
    ent = await _entity(session, state="WA")
    sig = await _signal(session, confidence=0.1, observed_at=NOW - timedelta(days=365), entity=ent)

    result = await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws, icp=icp, now=NOW
    )
    await session.commit()

    assert result is None
    assert await _scores(session, ws) == []


async def test_prefilter_miss_writes_no_row(session: AsyncSession) -> None:
    ws = await _workspace(session, email="c@example.com")
    icp = await _icp(session, workspace_id=ws, signal_types=["rfp_posted"])
    ent = await _entity(session, state="WA")
    # Wrong signal type → pre-filter excludes it before scoring.
    sig = await _signal(session, signal_type="news_mention", entity=ent)

    result = await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws, icp=icp, now=NOW
    )
    await session.commit()
    assert result is None
    assert await _scores(session, ws) == []


# --- Workspace isolation ----------------------------------------------------


async def test_workspace_isolation(session: AsyncSession) -> None:
    ws_a = await _workspace(session, email="iso-a@example.com")
    ws_b = await _workspace(session, email="iso-b@example.com")
    # A wants WA rfps; B wants CA rfps. A WA signal must reach only A.
    icp_a = await _icp(session, workspace_id=ws_a, states=["WA"])
    icp_b = await _icp(session, workspace_id=ws_b, states=["CA"])
    ent = await _entity(session, state="WA")
    sig = await _signal(session, entity=ent)

    await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws_a, icp=icp_a, now=NOW
    )
    await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws_b, icp=icp_b, now=NOW
    )
    await session.commit()

    # A (WA) matches the WA signal; B (CA) does not. No cross-tenant leakage.
    rows_a = await _scores(session, ws_a)
    rows_b = await _scores(session, ws_b)
    assert len(rows_a) == 1
    assert rows_a[0].workspace_id == ws_a
    assert rows_a[0].signal_id == sig.id
    assert rows_b == []


async def test_unrestricted_icp_matches_unresolved_signal(session: AsyncSession) -> None:
    # An ICP that does not restrict by country/state/kind/size matches a signal whose
    # entity is unresolved (no geo/kind/size) — the empty-array "all values" rule
    # (doc 14 §6.1). Only the workspace whose signal-type interest matches gets a row.
    ws_a = await _workspace(session, email="ur-a@example.com")
    ws_b = await _workspace(session, email="ur-b@example.com")
    await _icp(
        session,
        workspace_id=ws_a,
        states=[],
        entity_kinds=[],
        signal_types=["rfp_posted"],
        countries=[],
    )
    await _icp(
        session,
        workspace_id=ws_b,
        states=[],
        entity_kinds=[],
        signal_types=["grant_awarded"],
        countries=[],
    )
    sig = await _signal(session, signal_type="rfp_posted")  # unresolved entity

    await services.score_signal_for_all_workspaces(session, signal_id=sig.id, now=NOW)
    await session.commit()

    rows_a = await _scores(session, ws_a)
    rows_b = await _scores(session, ws_b)
    assert len(rows_a) == 1  # A wants rfp_posted → matched
    assert rows_b == []  # B wants grant_awarded → no row


# --- The signal.created trigger fan-out (doc 14 §4.1, §6) -------------------


async def test_score_signal_for_all_workspaces_fans_out(session: AsyncSession) -> None:
    ws1 = await _workspace(session, email="fan1@example.com")
    ws2 = await _workspace(session, email="fan2@example.com")
    ws3 = await _workspace(session, email="fan3@example.com")
    await _icp(session, workspace_id=ws1, states=[], countries=[], signal_types=["rfp_posted"])
    await _icp(session, workspace_id=ws2, states=[], countries=[], signal_types=["rfp_posted"])
    await _icp(session, workspace_id=ws3, states=[], countries=[], signal_types=["grant_awarded"])
    sig = await _signal(session, signal_type="rfp_posted")

    written = await services.score_signal_for_all_workspaces(session, signal_id=sig.id, now=NOW)
    await session.commit()

    # ws1 + ws2 want rfp_posted; ws3 wants grant_awarded → 2 rows written.
    assert written == 2
    assert len(await _scores(session, ws1)) == 1
    assert len(await _scores(session, ws2)) == 1
    assert await _scores(session, ws3) == []


async def test_signal_created_event_triggers_scoring(session: AsyncSession) -> None:
    """The ``signal.created`` listener scores a new signal for matching workspaces."""
    from civicsignals_api.modules.signals import listeners

    ws = await _workspace(session, email="evt@example.com")
    await _icp(session, workspace_id=ws, states=[], countries=[], signal_types=["rfp_posted"])
    sig = await _signal(session, signal_type="rfp_posted")
    await session.commit()

    # The listener opens its own SessionLocal-backed session against the same DSN.
    # In CI the app DSN == the test DSN, so the listener sees the committed signal.
    listeners.register_listeners()
    try:
        from civicsignals_api import events

        await events.publish(events.SIGNAL_CREATED, {"signal_id": str(sig.id)})
    finally:
        listeners.unregister_listeners()

    # The listener committed in its own session; re-read the (workspace, signal)
    # ids in ours via a scalar query so no lazy attribute load is needed.
    matched = (
        (
            await session.execute(
                select(WorkspaceScore.signal_id).where(WorkspaceScore.workspace_id == ws)
            )
        )
        .scalars()
        .all()
    )
    assert list(matched) == [sig.id]


# --- Idempotent upsert + stale-row removal (doc 14 §7.3) --------------------


async def test_rescore_is_idempotent_upsert(session: AsyncSession) -> None:
    ws = await _workspace(session, email="upsert@example.com")
    icp = await _icp(session, workspace_id=ws, states=[], countries=[], signal_types=["rfp_posted"])
    sig = await _signal(session, signal_type="rfp_posted")

    await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws, icp=icp, now=NOW
    )
    await session.commit()
    rows = await _scores(session, ws)
    assert len(rows) == 1
    first_id = rows[0].id

    # Re-score: still one row (ON CONFLICT update), and the user-owned status is set
    # then preserved across the re-score (doc 14 §7.3 — existing rows not reset).
    rows[0].status = SCORE_STATUS_DISMISSED
    await session.commit()
    await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws, icp=icp, now=NOW
    )
    await session.commit()
    rows = await _scores(session, ws)
    assert len(rows) == 1
    assert rows[0].id == first_id  # same row, updated in place
    assert rows[0].status == SCORE_STATUS_DISMISSED  # status preserved


# --- The G1 feed: ranked by score + cursor pagination (doc 14 §5.3) ---------


async def test_list_workspace_signals_ranked_and_paginated(session: AsyncSession) -> None:
    ws = await _workspace(session, email="feed@example.com")
    icp = await _icp(
        session,
        workspace_id=ws,
        states=[],
        countries=[],
        signal_types=["rfp_posted"],
        keywords_required=[],
    )

    # Three signals with descending recency → descending recency component →
    # descending score (all else equal). Score them all.
    sigs = []
    for i, days in enumerate((0, 20, 60)):
        s = await _signal(
            session,
            title=f"RFP {i}",
            summary="productivity software rfp",
            observed_at=NOW - timedelta(days=days),
        )
        sigs.append(s)
        await services.score_signal_for_workspace(
            session, signal_id=s.id, workspace_id=ws, icp=icp, now=NOW
        )
    await session.commit()

    # Page 1 (limit 2): the two highest-scoring (freshest) first, ranked desc.
    page1 = await services.list_workspace_signals(session, workspace_id=ws, limit=2)
    assert len(page1.items) == 2
    assert page1.items[0].score >= page1.items[1].score
    assert page1.next_cursor is not None
    assert page1.items[0].signal.id == sigs[0].id  # freshest = top

    # Page 2: the remaining item; no further cursor.
    page2 = await services.list_workspace_signals(
        session, workspace_id=ws, limit=2, cursor=page1.next_cursor
    )
    assert len(page2.items) == 1
    assert page2.next_cursor is None
    seen = {i.signal.id for i in page1.items} | {i.signal.id for i in page2.items}
    assert seen == {s.id for s in sigs}


async def test_feed_hides_dismissed_by_default(session: AsyncSession) -> None:
    ws = await _workspace(session, email="hide@example.com")
    icp = await _icp(
        session,
        workspace_id=ws,
        states=[],
        countries=[],
        signal_types=["rfp_posted"],
        keywords_required=[],
    )
    sig = await _signal(session, summary="productivity software rfp")
    await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws, icp=icp, now=NOW
    )
    await session.commit()

    rows = await _scores(session, ws)
    rows[0].status = SCORE_STATUS_DISMISSED
    await session.commit()

    # Default feed excludes dismissed...
    page = await services.list_workspace_signals(session, workspace_id=ws)
    assert page.items == []
    # ...but an explicit status filter can include it.
    page_all = await services.list_workspace_signals(
        session, workspace_id=ws, statuses=[SCORE_STATUS_DISMISSED]
    )
    assert len(page_all.items) == 1


async def test_feed_is_workspace_scoped(session: AsyncSession) -> None:
    ws_a = await _workspace(session, email="scope-a@example.com")
    ws_b = await _workspace(session, email="scope-b@example.com")
    icp_a = await _icp(
        session,
        workspace_id=ws_a,
        states=[],
        countries=[],
        signal_types=["rfp_posted"],
        keywords_required=[],
    )
    sig = await _signal(session, summary="productivity software rfp")
    await services.score_signal_for_workspace(
        session, signal_id=sig.id, workspace_id=ws_a, icp=icp_a, now=NOW
    )
    await session.commit()

    # ws_a sees the signal; ws_b (no score rows) sees an empty feed.
    page_a = await services.list_workspace_signals(session, workspace_id=ws_a)
    page_b = await services.list_workspace_signals(session, workspace_id=ws_b)
    assert len(page_a.items) == 1
    assert page_b.items == []
