"""DB-backed verification of the e2e seed harness (the routing invariant).

Runs ``seed_e2e`` against a real Postgres (pgvector) test database, then asserts the
**routing invariant** the whole e2e suite is built on:

- Alice's workspace (TX / school_district / rfp_posted) has a ``signals_workspace_score``
  for the TX RFP signal and **NOT** for the CA county-news signal.
- Bob's workspace (CA / news_mention) has the CA news signal and **NOT** the TX RFP.
- The weak-match signal (VT / library_system) scores into **no** workspace — it is
  stored globally but stays below every ICP's gate (the threshold/pre-filter proof).

Each workspace feed is read through ``signals.services.list_workspace_signals`` (the
G1 read seam). Also asserts the seeder is **idempotent**: a second run over the same
DB yields the same routing (no duplicate users / signals / score rows).

DSN-gated: skips cleanly when no Postgres DSN is configured (``DATABASE_DIRECT_URL``
/ ``DATABASE_URL`` / ``SIGNALS_TEST_DSN``). The schema uses pgvector + citext +
pg_trgm, which SQLite cannot replicate, so this is Postgres-only by design.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.config import Settings
from civicsignals_api.db import Base
from civicsignals_api.modules.signals import services as signals_services
from civicsignals_api.scripts import seed_e2e

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
_db_skip = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN, poolclass=NullPool)
    async with engine.begin() as conn:
        # Drop the whole schema (CASCADE handles cross-table FKs that
        # ``metadata.drop_all`` cannot order correctly when the DB also carries
        # migration-only objects) for a clean slate, then rebuild from the metadata.
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
            # ``DROP SCHEMA public CASCADE`` drops the extensions too; recreate them
            # (mirroring the root conftest ``_ensure_pg_extensions``) so a later suite
            # in the same CI process still finds the ``vector`` / ``citext`` /
            # ``pg_trgm`` types its ``create_all`` needs.
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await engine.dispose()


def _fake_settings() -> Settings:
    # Force the fake backend (no API keys). seed_e2e itself sets llm_fake_fixtures.
    return Settings(llm_backend="fake")


async def _feed_signal_ids(session: AsyncSession, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    page = await signals_services.list_workspace_signals(
        session, workspace_id=workspace_id, limit=signals_services.MAX_LIMIT
    )
    return {item.signal.id for item in page.items}


def _signal_id_for(summary: seed_e2e.SeedSummary, scenario_id: str) -> uuid.UUID:
    for sc in summary.scenarios:
        if sc.scenario_id == scenario_id:
            assert sc.signal_ids, f"scenario {scenario_id} produced no signal"
            return sc.signal_ids[0]
    raise AssertionError(f"scenario {scenario_id} not in summary")


def _workspace_for(summary: seed_e2e.SeedSummary, user_key: str) -> uuid.UUID:
    for u in summary.users:
        if u.key == user_key:
            return u.workspace_id
    raise AssertionError(f"user {user_key} not in summary")


@_db_skip
@pytest.mark.asyncio
async def test_seed_e2e_routing_invariant(session: AsyncSession) -> None:
    summary = await seed_e2e.seed_e2e(session, settings=_fake_settings())
    await session.commit()

    # Each scenario produced exactly one signal of the intended type.
    assert {sc.scenario_id for sc in summary.scenarios} == {
        "tx_school_rfp",
        "ca_county_news",
        "weak_match",
    }
    for sc in summary.scenarios:
        assert len(sc.signal_ids) == 1, f"{sc.scenario_id}: {sc.signal_ids}"
    tx_rfp = _signal_id_for(summary, "tx_school_rfp")
    ca_news = _signal_id_for(summary, "ca_county_news")
    weak = _signal_id_for(summary, "weak_match")

    alice_ws = _workspace_for(summary, "alice")
    bob_ws = _workspace_for(summary, "bob")

    alice_feed = await _feed_signal_ids(session, alice_ws)
    bob_feed = await _feed_signal_ids(session, bob_ws)

    # Alice (TX / school_district / rfp_posted): the TX RFP, not the CA news.
    assert tx_rfp in alice_feed, "Alice should see the TX school RFP"
    assert ca_news not in alice_feed, "Alice should NOT see the CA county news"

    # Bob (CA / news_mention): the CA news, not the TX RFP.
    assert ca_news in bob_feed, "Bob should see the CA county news"
    assert tx_rfp not in bob_feed, "Bob should NOT see the TX RFP"

    # Weak match (VT / library_system): scores into no workspace at all.
    assert weak not in alice_feed, "weak match must stay below Alice's strict threshold"
    assert weak not in bob_feed, "weak match must not score into Bob's feed"


@_db_skip
@pytest.mark.asyncio
async def test_seed_e2e_is_idempotent(session: AsyncSession) -> None:
    first = await seed_e2e.seed_e2e(session, settings=_fake_settings())
    await session.commit()
    second = await seed_e2e.seed_e2e(session, settings=_fake_settings())
    await session.commit()

    # Same users (by email) and same workspaces across both runs.
    assert {u.email for u in first.users} == {u.email for u in second.users}
    assert {u.workspace_id for u in first.users} == {u.workspace_id for u in second.users}

    # No duplicate signals: each scenario still yields one signal id, unchanged.
    for sc1, sc2 in zip(
        sorted(first.scenarios, key=lambda s: s.scenario_id),
        sorted(second.scenarios, key=lambda s: s.scenario_id),
        strict=True,
    ):
        assert sc1.signal_ids == sc2.signal_ids, f"{sc1.scenario_id} signals changed on reseed"

    # Routing unchanged on the second run.
    alice_ws = _workspace_for(second, "alice")
    bob_ws = _workspace_for(second, "bob")
    alice_feed = await _feed_signal_ids(session, alice_ws)
    bob_feed = await _feed_signal_ids(session, bob_ws)
    assert _signal_id_for(second, "tx_school_rfp") in alice_feed
    assert _signal_id_for(second, "ca_county_news") in bob_feed
