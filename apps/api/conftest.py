"""Root pytest conftest for the API test suite.

Several module test suites build their schema with an (unfiltered)
``Base.metadata.create_all`` against the configured test Postgres. As models grow,
some columns require Postgres *extensions* to even create the table:

- ``citext`` — case-insensitive email columns (auth/accounts).
- ``pg_trgm`` — the trigram GIN index on ``entities_entity.name`` (C1).
- ``vector`` — the ``signals_signal.vector_embedding`` pgvector column (E4/doc 07);
  populated by I1.

The dev stack and CI both run the ``pgvector/pgvector:pg16`` image, which *ships*
these extensions but does not ``CREATE EXTENSION`` them. Each create_all site
historically created the one extension it needed; that does not scale as a new
globally-discovered model adds a new extension dependency to *every* unfiltered
create_all. This session-scoped autouse fixture creates the required extensions
once, up front, so any test's ``create_all`` succeeds regardless of which module's
columns are in the metadata. It is a no-op when no DB DSN is configured (the many
DB-gated suites simply skip).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest

_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")

# Extensions any unfiltered Base.metadata.create_all may need (see module docstring).
_REQUIRED_EXTENSIONS = ("citext", "pg_trgm", "vector")


@pytest.fixture(scope="session", autouse=True)
def _ensure_pg_extensions() -> Iterator[None]:
    """Create the Postgres extensions the test schema needs (no-op without a DSN)."""
    if _DSN:
        asyncio.run(_create_extensions(_DSN))
    yield


async def _create_extensions(dsn: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            for ext in _REQUIRED_EXTENSIONS:
                await conn.execute(text(f"CREATE EXTENSION IF NOT EXISTS {ext}"))
    finally:
        await engine.dispose()
