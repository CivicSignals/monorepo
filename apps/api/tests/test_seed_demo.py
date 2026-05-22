"""Tests for the demo seed (TODO A2).

The fixture-consistency checks run everywhere (no DB needed). The end-to-end
idempotency test runs only when a Postgres URL is provided via
``SEED_DEMO_TEST_DSN`` (CI / a developer with the dev stack up); otherwise it
skips, so the suite stays green without a database.
"""

from __future__ import annotations

import os

import pytest

from civicsignals_api.scripts import seed_demo


def test_entity_slugs_unique() -> None:
    slugs = [e["slug"] for e in seed_demo.DEMO_ENTITIES]
    assert len(slugs) == len(set(slugs))


def test_signal_dedupe_keys_unique() -> None:
    keys = [s["dedupe_key"] for s in seed_demo.DEMO_SIGNALS]
    assert len(keys) == len(set(keys))


def test_every_signal_references_a_defined_entity() -> None:
    entity_slugs = {e["slug"] for e in seed_demo.DEMO_ENTITIES}
    for signal in seed_demo.DEMO_SIGNALS:
        assert signal["entity_slug"] in entity_slugs


def test_signal_scores_in_range() -> None:
    for signal in seed_demo.DEMO_SIGNALS:
        score = signal["score"]
        assert isinstance(score, int)
        assert 0 <= score <= 100


_SEED_TABLES = (
    "dev_seed_workspace",
    "dev_seed_user",
    "dev_seed_entity",
    "dev_seed_signal",
)


@pytest.mark.asyncio
async def test_seed_is_idempotent(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Running the seed twice yields the same row counts (no duplicates)."""
    dsn = os.environ.get("SEED_DEMO_TEST_DSN")
    if not dsn:
        pytest.skip("SEED_DEMO_TEST_DSN not set; needs a live Postgres")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # Point the seed at the test DSN. The seed resolves ``database_direct_url``
    # first, so override the env var and drop the lru_cache so a fresh Settings
    # is built. `monkeypatch` reverts the env var; the finalizer clears the cache
    # again afterwards so the test-scoped DSN never leaks into another test's
    # cached Settings.
    monkeypatch.setenv("DATABASE_DIRECT_URL", dsn)
    seed_demo.get_settings.cache_clear()
    request.addfinalizer(seed_demo.get_settings.cache_clear)

    engine = create_async_engine(dsn)

    async def counts() -> dict[str, int]:
        async with engine.connect() as conn:
            result: dict[str, int] = {}
            for table in _SEED_TABLES:
                row = await conn.execute(text("SELECT count(*) FROM " + table))
                result[table] = int(row.scalar_one())
            return result

    try:
        await seed_demo.seed()
        first = await counts()
        await seed_demo.seed()
        second = await counts()
        assert first == second
        assert second["dev_seed_workspace"] == 1
        assert second["dev_seed_entity"] == len(seed_demo.DEMO_ENTITIES)
        assert second["dev_seed_signal"] == len(seed_demo.DEMO_SIGNALS)
    finally:
        await engine.dispose()
