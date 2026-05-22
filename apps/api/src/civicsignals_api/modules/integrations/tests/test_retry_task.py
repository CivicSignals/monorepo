"""DB-backed tests for the retry sweeper (K1; ``retry_failed_pushes``).

Seeds a connection + a ``failed`` push-log row that is due for retry, then runs
the async sweeper and asserts the row is re-attempted (succeeds → ``success``;
fails again → backoff or dead-letter). Skips without a DSN.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.config import get_settings
from civicsignals_api.modules.integrations import providers, services
from civicsignals_api.modules.integrations.models import (
    Connection,
    ConnectionStatus,
    IntegrationProviderKind,
    PushErrorCode,
    PushLog,
    PushStatus,
)
from civicsignals_api.modules.integrations.providers import (
    IntegrationProvider,
    OAuth2AuthorizationCodeMixin,
    OAuthConfig,
    ProviderError,
    PushRequest,
    PushResult,
)
from civicsignals_api.modules.integrations.tasks import _retry_failed_pushes_async
from civicsignals_api.modules.integrations.tests.conftest import _require_db


class _ConfiguredProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
    kind = IntegrationProviderKind.SALESFORCE
    _result: PushResult | None = None
    _error: ProviderError | None = None

    def oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            authorize_url="https://p/a",
            token_url="https://p/t",
            scopes=("read",),
            client_id="cid",
            client_secret="csecret",
        )

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def _seed_due_failed_push() -> tuple[str, str]:
    """Seed a workspace + connection + a due-for-retry failed push. Returns ids."""
    conn_id = uuid.uuid4()
    log_id = uuid.uuid4()
    cipher = services.token_cipher(get_settings())

    async def _do() -> None:
        from civicsignals_api.modules.accounts import services as accounts_services

        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            user = await accounts_services.create_user(
                session,
                email=f"retry-{uuid.uuid4().hex[:8]}@example.com",
                password_hash="x",
                name="Retry",
                email_verified=True,
            )
            workspace = await accounts_services.create_workspace(
                session, name="Retry WS", owner=user
            )
            await session.flush()
            conn = Connection(
                id=conn_id,
                workspace_id=workspace.id,
                provider=IntegrationProviderKind.SALESFORCE,
                name="Retry conn",
                status=ConnectionStatus.DEGRADED,
            )
            conn.access_token_encrypted = cipher.encrypt("live-token")
            conn.token_expires_at = datetime.now(UTC) + timedelta(hours=1)
            session.add(conn)
            log = PushLog(
                id=log_id,
                workspace_id=conn.workspace_id,
                connection_id=conn_id,
                target="salesforce.opportunity",
                request={"name": "Acme"},
                status=PushStatus.FAILED,
                error_code=PushErrorCode.TRANSIENT,
                error_message="503",
                attempt_count=1,
                retry_at=datetime.now(UTC) - timedelta(minutes=1),  # due
            )
            session.add(log)
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())
    return str(conn_id), str(log_id)


async def _run_sweeper() -> int:
    """Dispose the shared ``db.engine`` around the sweeper run.

    ``_retry_failed_pushes_async`` uses the module-level ``SessionLocal`` (db.py).
    Its engine pools connections, which would otherwise be bound to a previous
    ``asyncio.run`` loop ("Event loop is closed"). Disposing before and after
    forces a fresh connection on this loop.
    """
    from civicsignals_api import db

    await db.engine.dispose()
    try:
        return await _retry_failed_pushes_async()
    finally:
        await db.engine.dispose()


def _load_push_log(log_id: str) -> PushLog:
    async def _do() -> PushLog:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            row = (
                await session.execute(select(PushLog).where(PushLog.id == uuid.UUID(log_id)))
            ).scalar_one()
            await engine.dispose()
            return row

    return asyncio.run(_do())


def test_retry_task_resends_due_failed_push(
    client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A due failed push is re-attempted; a success flips it to success."""
    _require_db()

    class _OK(_ConfiguredProvider):
        _result = PushResult(external_id="0061T-OK")

    monkeypatch.setitem(providers.REGISTRY, IntegrationProviderKind.SALESFORCE, _OK)
    monkeypatch.setattr(
        services,
        "default_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    _conn_id, log_id = _seed_due_failed_push()

    attempted = asyncio.run(_run_sweeper())
    assert attempted == 1

    row = _load_push_log(log_id)
    assert row.status == PushStatus.SUCCESS
    assert row.external_id == "0061T-OK"
    assert row.attempt_count == 2


def test_retry_task_reschedules_on_repeat_failure(
    client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A still-failing transient push is rescheduled (more attempts remain)."""
    _require_db()

    class _Boom(_ConfiguredProvider):
        _error = ProviderError(PushErrorCode.TRANSIENT, "still down")

    monkeypatch.setitem(providers.REGISTRY, IntegrationProviderKind.SALESFORCE, _Boom)
    monkeypatch.setattr(
        services,
        "default_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    _conn_id, log_id = _seed_due_failed_push()

    asyncio.run(_run_sweeper())

    row = _load_push_log(log_id)
    # attempt 2 of default 5 → still FAILED, rescheduled into the future.
    assert row.status == PushStatus.FAILED
    assert row.attempt_count == 2
    assert row.retry_at is not None
    assert row.retry_at > datetime.now(UTC)
