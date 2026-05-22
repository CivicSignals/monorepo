"""Test fixtures for the admin module (B9 audit events).

Needs Postgres (CITEXT + UUID + JSONB columns); skips when no DSN is
configured. The ``citext`` and ``pgcrypto`` extensions are ensured first.
``Base.metadata.create_all`` builds the schema from Python models so the
fixture is independent of Alembic migration ordering.

``TestClient`` drives the app synchronously (its own event loop), so the engine
uses ``NullPool`` to prevent asyncpg connections from leaking across requests.
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
from civicsignals_api.modules.auth.models import (  # noqa: F401
    EmailVerificationToken,
)

_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")


def _require_db() -> str:
    if not _DSN:
        pytest.skip("DATABASE_DIRECT_URL/DATABASE_URL not set; audit tests need Postgres")
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
