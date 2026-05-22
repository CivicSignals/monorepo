"""DB fixtures for notifications digest tests (H3).

The digest subscription table uses Postgres-specific features (ON CONFLICT upsert,
partial index, JSONB on the joined saved-search row), so these build an engine from
``DATABASE_DIRECT_URL``/``DATABASE_URL`` and skip when neither is set — the same
pattern as the searches/icp/accounts conftests. CI provides a pgvector Postgres.

**Database isolation.** Each test resets the *whole* schema via
``Base.metadata.drop_all``/``create_all`` (independent of migration ordering). To
avoid colliding with the searches conftest, which resets the *same* shared ``Base``
schema per-test, this module runs against a **sibling database**
(``<configured-db>_notifications_h3``), created on first use. That way one module's
``drop_all`` never wipes another module's in-flight test data when the scoped gate
runs both directories in a single pytest invocation.

``TestClient`` drives the app in its own loop, so the engine uses ``NullPool``
(asyncpg connections bind to the loop that opened them).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.main import app

# Register every table the digest path touches (saved search -> signals feed ->
# notifications subscription) so create_all builds a complete schema.
from civicsignals_api.modules.accounts.models import (  # noqa: F401
    Membership,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.auth.models import EmailVerificationToken  # noqa: F401
from civicsignals_api.modules.notifications.models import DigestSubscription  # noqa: F401
from civicsignals_api.modules.searches.models import SavedSearch  # noqa: F401
from civicsignals_api.modules.signals.models import Signal  # noqa: F401
from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore  # noqa: F401

_BASE_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")
# Sibling DB so this module's per-test schema reset can't race the searches
# conftest's reset of the same shared ``Base`` on the configured DB.
_TEST_DB_SUFFIX = "_notifications_h3"


def _isolated_dsn(base_dsn: str) -> tuple[str, str]:
    """Return ``(sibling_dsn, sibling_db_name)`` derived from ``base_dsn``."""
    parts = urlsplit(base_dsn)
    base_db = parts.path.lstrip("/") or "civicsignals"
    sibling_db = f"{base_db}{_TEST_DB_SUFFIX}"
    sibling = urlunsplit(parts._replace(path=f"/{sibling_db}"))
    return sibling, sibling_db


def require_db() -> str:
    if not _BASE_DSN:
        pytest.skip("DATABASE_DIRECT_URL/DATABASE_URL not set; digest tests need Postgres")
    return _isolated_dsn(_BASE_DSN)[0]


async def _ensure_database(base_dsn: str) -> None:
    """Create the sibling test DB if it does not exist (CREATE DATABASE is autocommit)."""
    _sibling, sibling_db = _isolated_dsn(base_dsn)
    # Connect to the configured DB to issue CREATE DATABASE.
    admin = create_async_engine(base_dsn, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": sibling_db},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{sibling_db}"'))
    finally:
        await admin.dispose()


async def _reset_schema(dsn: str) -> None:
    engine = create_async_engine(dsn, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture
def db_engine() -> Iterator[AsyncEngine]:
    dsn = require_db()
    assert _BASE_DSN is not None  # require_db skips otherwise
    asyncio.run(_ensure_database(_BASE_DSN))
    asyncio.run(_reset_schema(dsn))
    engine = create_async_engine(dsn, poolclass=NullPool)
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)


@pytest.fixture
def client(db_engine: AsyncEngine) -> Iterator[TestClient]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)
