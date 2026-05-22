"""Async SQLAlchemy 2.0 engine + session, and the shared declarative base.

Every module owns its own tables (prefixed, e.g. ``signals_signal``) and is the
only module that migrates them (doc 06 §3, §4). Models import ``Base`` from here
so Alembic autogenerate sees one metadata object.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from civicsignals_api.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by all module models."""


_settings = get_settings()

# Pooling is handled by PgBouncer (transaction mode), so disable SQLAlchemy's
# own pooling on the app side to avoid double-pooling surprises.
engine = create_async_engine(
    _settings.database_url,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with SessionLocal() as session:
        yield session
