"""Test fixtures for the accounts module (B5 workspaces).

The workspace create/list/switch flow needs Postgres (CITEXT + UUID columns),
so this builds an engine from ``DATABASE_DIRECT_URL``/``DATABASE_URL`` and skips
when neither is set — the same pattern as the auth module's ``conftest``. CI
provides a pgvector Postgres service, so the flow runs there.

The schema is created with ``Base.metadata.create_all`` rather than Alembic,
keeping the fixture independent of migration ordering; the ``citext`` extension
is ensured first. ``TestClient`` drives the app in its own loop, so the per-test
engine uses ``NullPool`` (asyncpg connections bind to the loop that opened them).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.main import app
from civicsignals_api.modules.accounts.models import (  # noqa: F401  register tables
    Membership,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.auth.models import (  # noqa: F401  register tables
    EmailVerificationToken,
)

_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")


def _require_db() -> str:
    if not _DSN:
        pytest.skip("DATABASE_DIRECT_URL/DATABASE_URL not set; workspace flow needs Postgres")
    return _DSN


async def _reset_schema(dsn: str) -> None:
    engine = create_async_engine(dsn, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture
def client() -> Iterator[TestClient]:
    dsn = _require_db()
    asyncio.run(_reset_schema(dsn))

    engine = create_async_engine(dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)
    asyncio.run(engine.dispose())
