"""Test fixtures for the auth module.

Pure-logic tests (JWT, password hashing) need no database and always run.
The end-to-end signup/login/verify flow needs Postgres (CITEXT + UUID columns),
so these fixtures build an engine from ``DATABASE_DIRECT_URL``/``DATABASE_URL``
and skip when neither is set — the same pattern as ``tests/test_seed_demo.py``.
CI provides a pgvector Postgres service, so the flow runs there.

The schema is created with ``Base.metadata.create_all`` (every module's models
import ``Base``; only B1 defines concrete tables so far) rather than Alembic,
keeping the fixture independent of migration ordering. The ``citext`` extension
is ensured first.

``TestClient`` drives the FastAPI app in its own event loop (via an anyio
portal). asyncpg connections bind to the loop that opened them, so the per-test
engine uses ``NullPool`` and opens a fresh connection inside each request — under
the app's loop — rather than reusing a pooled connection created in pytest's
loop (which would raise "attached to a different loop").
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.main import app
from civicsignals_api.modules.accounts.models import (  # noqa: F401  registers the tables
    Membership,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.auth.models import (  # noqa: F401  registers the tables
    ApiToken,
    EmailVerificationToken,
    MfaBackupCode,
    MfaCredential,
    OAuthIdentity,
    PasswordResetToken,
)
from civicsignals_api.modules.notifications import services as notifications_services

_DSN = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")


def _require_db() -> str:
    if not _DSN:
        pytest.skip("DATABASE_DIRECT_URL/DATABASE_URL not set; auth flow needs Postgres")
    return _DSN


async def _reset_schema(dsn: str) -> None:
    engine = create_async_engine(dsn, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture
def recorder() -> Iterator[notifications_services.RecordingEmailSender]:
    """Capture outbound mail so the verification link can be asserted."""
    rec = notifications_services.RecordingEmailSender()
    original: Callable[[], notifications_services.EmailSender] = (
        notifications_services.default_email_sender
    )

    def _factory() -> notifications_services.EmailSender:
        return rec

    notifications_services.default_email_sender = _factory
    yield rec
    notifications_services.default_email_sender = original


@pytest.fixture
def client(
    recorder: notifications_services.RecordingEmailSender,
) -> Iterator[TestClient]:
    dsn = _require_db()
    # Reset the schema in a throwaway loop before the app's loop starts.
    asyncio.run(_reset_schema(dsn))

    # NullPool: each request opens its own connection in the app's event loop.
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
