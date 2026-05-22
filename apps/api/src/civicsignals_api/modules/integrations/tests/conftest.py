"""Test fixtures for the integrations module (K1).

Live-DB tests need Postgres (UUID + JSONB + ARRAY columns); they skip when no
DSN is configured. ``Base.metadata.create_all`` builds the schema from the
Python models so the fixture is independent of Alembic ordering.

``TestClient`` drives the app synchronously (its own event loop), so the engine
uses ``NullPool`` to prevent asyncpg connections leaking across requests.
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

# Register all models so Base.metadata is complete.
from civicsignals_api.modules.accounts.models import (  # noqa: F401
    Membership,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.admin.models import AuditEvent  # noqa: F401
from civicsignals_api.modules.auth.models import EmailVerificationToken  # noqa: F401
from civicsignals_api.modules.integrations.models import (  # noqa: F401
    Connection,
    FieldMapping,
    PushLog,
)

_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")


def _require_db() -> str:
    if not _DSN:
        pytest.skip("DATABASE_DIRECT_URL/DATABASE_URL not set; integrations tests need Postgres")
    return _DSN


async def _reset_schema(dsn: str) -> None:
    engine = create_async_engine(dsn, poolclass=NullPool)
    async with engine.begin() as conn:
        # DROP SCHEMA ... CASCADE rather than ``metadata.drop_all`` so the reset
        # is independent of cross-module FK ordering (e.g. ingestion → entities)
        # and works whether the DB started empty or already migrated.
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        # pgvector — signals_signal carries a VECTOR column; the full
        # Base.metadata create_all needs the extension present.
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
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
