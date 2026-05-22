# SPDX-License-Identifier: AGPL-3.0-only
"""DB-backed tests for the F5 feedback loop (doc 14 §12 "negative training").

Exercise the parts that need Postgres: the ``signals_signal_feedback`` upsert
(record / change / retract), the per-signal-type relevance aggregate, workspace +
user isolation, the ``wrong_extraction`` count (which must NOT alter scoring), the
``scoring_config_for_workspace`` no-op-without-feedback guarantee, and that a
not_relevant-nudged config lowers a re-score vs. the baseline.

Run against a live Postgres resolved from ``SIGNALS_TEST_DSN`` / ``DATABASE_DIRECT_URL``
/ ``DATABASE_URL``; skips cleanly when none is set (the tables use JSONB / TEXT[] /
Numeric / a pgvector FK target, so SQLite is not a faithful substitute).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base
from civicsignals_api.modules.accounts.models import (  # noqa: F401
    Membership,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.icp.models import IcpDefinition
from civicsignals_api.modules.signals import services
from civicsignals_api.modules.signals.models import Signal
from civicsignals_api.modules.signals.models_feedback import (
    FEEDBACK_NOT_RELEVANT,
    FEEDBACK_RELEVANT,
    FEEDBACK_WRONG_EXTRACTION,
    SignalFeedback,
)
from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore
from civicsignals_api.modules.signals.workspace_scoring import (
    DEFAULT_SCORING_CONFIG,
    MIN_FEEDBACK_FOR_OVERRIDE,
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


async def _user(session: AsyncSession, *, email: str) -> uuid.UUID:
    user = User(id=uuid.uuid4(), email=email, password_hash="x", name="T")
    session.add(user)
    await session.flush()
    return user.id


async def _workspace(session: AsyncSession, *, owner_id: uuid.UUID) -> uuid.UUID:
    org = Organization(id=uuid.uuid4(), name="Org")
    session.add(org)
    await session.flush()
    ws = Workspace(
        id=uuid.uuid4(),
        organization_id=org.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:12]}",
        owner_id=owner_id,
    )
    session.add(ws)
    await session.flush()
    return ws.id


async def _signal(session: AsyncSession, *, signal_type: str = "rfp_posted") -> Signal:
    sig = Signal(
        id=uuid.uuid4(),
        entity_id=None,
        signal_type=signal_type,
        recipe_id="r",
        raw_document_ids=[],
        content_hash=uuid.uuid4().hex,
        observed_at=NOW,
        title="RFP: productivity software",
        summary="RFP for productivity software for the district.",
        details={},
        confidence=0.9,
    )
    session.add(sig)
    await session.flush()
    return sig


async def _icp(session: AsyncSession, *, workspace_id: uuid.UUID) -> IcpDefinition:
    icp = IcpDefinition(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Test ICP",
        countries=[],
        states=[],
        entity_kinds=[],
        signal_types=["rfp_posted"],
        signal_weights={"rfp_posted": 1.0},
        keywords_required=[],
        keywords_excluded=[],
        threshold=10,
        is_active=True,
    )
    session.add(icp)
    await session.flush()
    return icp


async def _rows(session: AsyncSession, workspace_id: uuid.UUID) -> list[SignalFeedback]:
    res = await session.execute(
        select(SignalFeedback).where(SignalFeedback.workspace_id == workspace_id)
    )
    return list(res.scalars().all())


# --- Record / change / retract (doc 14 §12) ---------------------------------


async def test_record_then_change_then_retract(session: AsyncSession) -> None:
    user = await _user(session, email="fb-a@example.com")
    ws = await _workspace(session, owner_id=user)
    sig = await _signal(session)

    # Record.
    row = await services.set_signal_feedback(
        session, workspace_id=ws, signal_id=sig.id, user_id=user, kind=FEEDBACK_RELEVANT
    )
    await session.commit()
    assert row.kind == FEEDBACK_RELEVANT
    assert len(await _rows(session, ws)) == 1

    # Change (upsert flips the kind, no second row).
    row = await services.set_signal_feedback(
        session, workspace_id=ws, signal_id=sig.id, user_id=user, kind=FEEDBACK_NOT_RELEVANT
    )
    await session.commit()
    assert row.kind == FEEDBACK_NOT_RELEVANT
    assert len(await _rows(session, ws)) == 1
    assert (
        await services.get_user_signal_feedback(
            session, workspace_id=ws, signal_id=sig.id, user_id=user
        )
        == FEEDBACK_NOT_RELEVANT
    )

    # Retract.
    removed = await services.clear_signal_feedback(
        session, workspace_id=ws, signal_id=sig.id, user_id=user
    )
    await session.commit()
    assert removed is True
    assert await _rows(session, ws) == []
    # Retracting again is a no-op (idempotent).
    assert (
        await services.clear_signal_feedback(
            session, workspace_id=ws, signal_id=sig.id, user_id=user
        )
        is False
    )


async def test_unknown_kind_is_rejected(session: AsyncSession) -> None:
    user = await _user(session, email="fb-bad@example.com")
    ws = await _workspace(session, owner_id=user)
    sig = await _signal(session)
    with pytest.raises(services.FeedbackKindError):
        await services.set_signal_feedback(
            session, workspace_id=ws, signal_id=sig.id, user_id=user, kind="bogus"
        )


# --- Workspace + user isolation (doc 14 §12) --------------------------------


async def test_feedback_is_workspace_isolated(session: AsyncSession) -> None:
    user = await _user(session, email="fb-iso@example.com")
    ws_a = await _workspace(session, owner_id=user)
    ws_b = await _workspace(session, owner_id=user)
    sig = await _signal(session)

    await services.set_signal_feedback(
        session, workspace_id=ws_a, signal_id=sig.id, user_id=user, kind=FEEDBACK_NOT_RELEVANT
    )
    await session.commit()

    # Workspace B sees none of A's feedback.
    assert await _rows(session, ws_b) == []
    assert (
        await services.get_user_signal_feedback(
            session, workspace_id=ws_b, signal_id=sig.id, user_id=user
        )
        is None
    )
    counts_b = await services.feedback_counts_by_signal_type(session, workspace_id=ws_b)
    assert counts_b == {}


async def test_two_users_have_independent_verdicts(session: AsyncSession) -> None:
    user_a = await _user(session, email="fb-u1@example.com")
    user_b = await _user(session, email="fb-u2@example.com")
    ws = await _workspace(session, owner_id=user_a)
    sig = await _signal(session)

    await services.set_signal_feedback(
        session, workspace_id=ws, signal_id=sig.id, user_id=user_a, kind=FEEDBACK_RELEVANT
    )
    await services.set_signal_feedback(
        session, workspace_id=ws, signal_id=sig.id, user_id=user_b, kind=FEEDBACK_NOT_RELEVANT
    )
    await session.commit()
    # Both verdicts coexist (one per user); the aggregate counts both.
    assert len(await _rows(session, ws)) == 2
    counts = await services.feedback_counts_by_signal_type(session, workspace_id=ws)
    assert counts["rfp_posted"] == (1, 1)


# --- Aggregate + wrong_extraction handling (doc 14 §12) ----------------------


async def test_aggregate_counts_relevant_vs_not_relevant_and_excludes_wrong_extraction(
    session: AsyncSession,
) -> None:
    ws_owner = await _user(session, email="fb-agg-owner@example.com")
    ws = await _workspace(session, owner_id=ws_owner)
    sig_rfp_1 = await _signal(session, signal_type="rfp_posted")
    sig_rfp_2 = await _signal(session, signal_type="rfp_posted")
    sig_news = await _signal(session, signal_type="news_mention")

    # Three users so the unique (ws, signal, user) key allows multiple verdicts/signal.
    users = [await _user(session, email=f"fb-agg-{i}@example.com") for i in range(3)]
    await services.set_signal_feedback(
        session, workspace_id=ws, signal_id=sig_rfp_1.id, user_id=users[0], kind=FEEDBACK_RELEVANT
    )
    await services.set_signal_feedback(
        session, workspace_id=ws, signal_id=sig_rfp_2.id, user_id=users[1], kind=FEEDBACK_RELEVANT
    )
    await services.set_signal_feedback(
        session,
        workspace_id=ws,
        signal_id=sig_news.id,
        user_id=users[2],
        kind=FEEDBACK_NOT_RELEVANT,
    )
    # A wrong_extraction flag must NOT appear in the relevance aggregate.
    await services.set_signal_feedback(
        session,
        workspace_id=ws,
        signal_id=sig_rfp_1.id,
        user_id=users[1],
        kind=FEEDBACK_WRONG_EXTRACTION,
    )
    await session.commit()

    counts = await services.feedback_counts_by_signal_type(session, workspace_id=ws)
    assert counts["rfp_posted"] == (2, 0)  # wrong_extraction excluded
    assert counts["news_mention"] == (0, 1)

    # wrong_extraction is surfaced as its own count (extraction-quality seam).
    assert await services.workspace_wrong_extraction_count(session, workspace_id=ws) == 1


# --- scoring_config_for_workspace (no-op without feedback, doc 14 §12) -------


async def test_scoring_config_is_baseline_without_feedback(session: AsyncSession) -> None:
    owner = await _user(session, email="fb-cfg-noop@example.com")
    ws = await _workspace(session, owner_id=owner)
    config = await services.scoring_config_for_workspace(session, workspace_id=ws)
    # Strict no-op: returns the unchanged baseline object (identity, not just equal).
    assert config is DEFAULT_SCORING_CONFIG
    assert config.signal_type_weight_overrides == {}


async def test_scoring_config_applies_bounded_nudge_with_feedback(session: AsyncSession) -> None:
    owner = await _user(session, email="fb-cfg-nudge@example.com")
    ws = await _workspace(session, owner_id=owner)
    sigs = [
        await _signal(session, signal_type="rfp_posted") for _ in range(MIN_FEEDBACK_FOR_OVERRIDE)
    ]
    users = [
        await _user(session, email=f"fb-cfg-nudge-{i}@example.com")
        for i in range(MIN_FEEDBACK_FOR_OVERRIDE)
    ]
    # Enough not_relevant verdicts to clear the volume floor → a downward nudge.
    for sig, user in zip(sigs, users, strict=True):
        await services.set_signal_feedback(
            session, workspace_id=ws, signal_id=sig.id, user_id=user, kind=FEEDBACK_NOT_RELEVANT
        )
    await session.commit()

    config = await services.scoring_config_for_workspace(session, workspace_id=ws)
    assert config is not DEFAULT_SCORING_CONFIG
    assert config.signal_type_weight_overrides["rfp_posted"] < 1.0


async def test_not_relevant_feedback_lowers_a_rescore(session: AsyncSession) -> None:
    owner = await _user(session, email="fb-rescore@example.com")
    ws = await _workspace(session, owner_id=owner)
    icp = await _icp(session, workspace_id=ws)
    target = await _signal(session, signal_type="rfp_posted")

    # Baseline score (no feedback).
    baseline = await services.score_signal_for_workspace(
        session, signal_id=target.id, workspace_id=ws, icp=icp, now=NOW
    )
    await session.commit()
    assert baseline is not None
    baseline_score = baseline.score

    # Pile up not_relevant feedback on other rfp_posted signals to nudge the type down.
    users = [
        await _user(session, email=f"fb-rescore-{i}@example.com")
        for i in range(MIN_FEEDBACK_FOR_OVERRIDE)
    ]
    for user in users:
        other = await _signal(session, signal_type="rfp_posted")
        await services.set_signal_feedback(
            session, workspace_id=ws, signal_id=other.id, user_id=user, kind=FEEDBACK_NOT_RELEVANT
        )
    await session.commit()

    # Re-score the target through the feedback-nudged config (the F6 backfill seam).
    written = await services.score_workspace_candidates(
        session, workspace_id=ws, signal_ids=[target.id], icp=icp, now=NOW
    )
    await session.commit()
    assert written == 1

    row = (
        await session.execute(
            select(WorkspaceScore)
            .where(WorkspaceScore.workspace_id == ws)
            .where(WorkspaceScore.signal_id == target.id)
        )
    ).scalar_one()
    assert float(row.score) < baseline_score
