"""DB-backed verification of the entity loader CLI (C1; doc 16 §4/§5/§6).

Drives ``python -m civicsignals_api.scripts.load_entities`` via its ``main()``
entry point against a real Postgres (pgvector) test database and asserts it:

- inserts entities + kinds + geo from a small CSV (the committed sample
  fixtures, so the test stays offline — no multi-GB download), and
- is **idempotent** on re-run (the loaders UPSERT on the natural key, so row
  counts converge).

The CLI is a *synchronous* entry point that drives its own ``asyncio.run``; the
tests are therefore plain (non-async) functions so they don't nest event loops.
Each helper that touches the DB opens and disposes its own engine via
``asyncio.run``.

DSN-gated: skips cleanly when no Postgres DSN is configured (``ENTITIES_TEST_DSN``
/ ``DATABASE_DIRECT_URL`` / ``DATABASE_URL``) — the same convention as the
entities module's DB tests. Postgres-only by design (the entities schema needs
``pg_trgm`` for the trigram name index, which SQLite cannot replicate).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Connection, Table, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from civicsignals_api.config import get_settings
from civicsignals_api.db import Base
from civicsignals_api.modules.entities import seeding
from civicsignals_api.modules.entities.models import Entity, EntityKind, Geo
from civicsignals_api.scripts import load_entities

_DSN = (
    os.environ.get("ENTITIES_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

_TABLE_NAMES = ("entities_entity", "entities_kind", "entities_geo")
_TABLES: list[Table] = [Base.metadata.tables[name] for name in _TABLE_NAMES]


def _drop_entities_cascade(conn: Connection) -> None:
    for tbl in _TABLE_NAMES:
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl} CASCADE")


async def _reset_schema() -> None:
    assert _DSN is not None
    engine = create_async_engine(_DSN)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            await conn.run_sync(_drop_entities_cascade)
            await conn.run_sync(Base.metadata.create_all, tables=_TABLES)
    finally:
        await engine.dispose()


async def _teardown_schema() -> None:
    assert _DSN is not None
    engine = create_async_engine(_DSN)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_entities_cascade)
    finally:
        await engine.dispose()


@pytest.fixture
def schema() -> Iterator[None]:
    """Create a fresh entities schema from the metadata, dropped after the test.

    ``get_settings()`` is repointed at the test DSN so the CLI (which reads the
    DB URL from settings) writes where the assertions read.
    """
    assert _DSN is not None
    os.environ["DATABASE_DIRECT_URL"] = _DSN
    get_settings.cache_clear()
    asyncio.run(_reset_schema())
    try:
        yield
    finally:
        asyncio.run(_teardown_schema())
        get_settings.cache_clear()


def _counts() -> dict[str, int]:
    async def _run() -> dict[str, int]:
        assert _DSN is not None
        engine = create_async_engine(_DSN)
        try:
            async with AsyncSession(engine) as session:
                out: dict[str, int] = {}
                for name, model in (("entity", Entity), ("kind", EntityKind), ("geo", Geo)):
                    result = await session.execute(select(func.count()).select_from(model))
                    out[name] = int(result.scalar_one())
                return out
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _school_district_count() -> int:
    async def _run() -> int:
        assert _DSN is not None
        engine = create_async_engine(_DSN)
        try:
            async with AsyncSession(engine) as session:
                n = (
                    await session.execute(
                        select(func.count())
                        .select_from(Entity)
                        .where(Entity.type == "school_district")
                    )
                ).scalar_one()
                return int(n)
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def test_cli_loads_entities_kinds_and_geo(schema: None) -> None:
    """`--source all` with the default sample fixtures inserts entities/kinds/geo."""
    rc = load_entities.main(["--source", "all"])
    assert rc == 0
    counts = _counts()
    # 6 NCES + 6 IPEDS + 6 census local-gov + 4 synthesized state roots.
    assert counts["entity"] == 6 + 6 + 6 + 4
    assert counts["kind"] == len(seeding.KIND_SEED)
    assert counts["geo"] > 0


def test_cli_is_idempotent_on_rerun(schema: None) -> None:
    """Re-running the loader converges to identical row counts (UPSERT keys)."""
    assert load_entities.main(["--source", "all"]) == 0
    first = _counts()
    assert load_entities.main(["--source", "all"]) == 0
    second = _counts()
    assert first == second


def test_cli_single_source_with_explicit_path(schema: None) -> None:
    """A single --source + explicit fixture path loads just that source's rows."""
    fixture = seeding.FIXTURES_DIR / "nces_ccd_sample.csv"
    assert load_entities.main(["--source", "nces", "--path-or-url", str(fixture)]) == 0
    # NCES districts present; IPEDS/Census default to their own sample fixtures.
    assert _school_district_count() == 6


def test_cli_rejects_path_with_source_all() -> None:
    """--path-or-url is per-source: combining it with --source all is an error."""
    with pytest.raises(SystemExit):
        load_entities.main(["--source", "all", "--path-or-url", str(Path("/tmp/x.csv"))])
