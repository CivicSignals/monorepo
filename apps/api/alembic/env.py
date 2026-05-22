"""Alembic async environment.

Imports every module's ``models`` so ``Base.metadata`` is fully populated for
autogenerate. Migrations run on the *direct* Postgres connection (bypassing
PgBouncer transaction-mode pooling). See doc 06 §4, doc 18 §6.3.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import pkgutil
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

from civicsignals_api import modules as modules_pkg
from civicsignals_api.config import get_settings
from civicsignals_api.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import all module model modules so their tables register on Base.metadata.
for _module in pkgutil.iter_modules(modules_pkg.__path__):
    if not _module.ispkg:
        continue
    with contextlib.suppress(ModuleNotFoundError):
        importlib.import_module(f"{modules_pkg.__name__}.{_module.name}.models")

target_metadata = Base.metadata

_settings = get_settings()
_db_url = _settings.database_direct_url or _settings.database_url


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = async_engine_from_config(
        {"sqlalchemy.url": _db_url},
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
