"""Live-DB API tests for K5 push-failure recovery.

Exercises the recovery surface end-to-end through the FastAPI app:

- ``GET /integrations/push-log/failures`` lists only failed/dead-letter rows,
  each with an inline ``diagnosis`` block (human-readable cause + CTA flags).
- The list is workspace-scoped (a member of workspace B never sees A's failures)
  and gated at ``RequireMember`` (viewers are 403; unauthenticated is 401).
- ``POST /integrations/push-log/{id}/retry`` re-runs the push through the K4
  idempotent path: a retry of a now-fixed push succeeds and appends a fresh
  push-log row; a retry of a non-failed row is 409; a missing/foreign row is 404.

Skips without a DSN (``DATABASE_DIRECT_URL`` / ``DATABASE_URL``).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.config import get_settings
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import MembershipRole
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
    PushRequest,
    PushResult,
)
from civicsignals_api.modules.integrations.tests.conftest import _require_db

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
FAILURES = "/api/v1/integrations/push-log/failures"
PASSWORD = "s3cur3-P4ssword!"


def _retry_url(push_log_id: str) -> str:
    return f"/api/v1/integrations/push-log/{push_log_id}/retry"


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _hdr(token: str, ws_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws_id}


def _create_workspace(client: TestClient, token: str, name: str = "K5 WS") -> dict[str, Any]:
    resp = client.post(WORKSPACES, json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _add_member_at_role(user_id: str, workspace_id: str, role: MembershipRole) -> None:
    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            await accounts_services.add_member(
                session,
                workspace_id=uuid.UUID(workspace_id),
                user_id=uuid.UUID(user_id),
                role=role,
            )
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())


# ---------------------------------------------------------------------------
# Test provider: configured + a fresh-token mock so retries don't hit network
# ---------------------------------------------------------------------------


class _RetryProvider(OAuth2AuthorizationCodeMixin, IntegrationProvider):
    """A provider whose push always succeeds (the 'now fixed' retry case)."""

    kind = IntegrationProviderKind.SALESFORCE

    def oauth_config(self) -> OAuthConfig:
        return OAuthConfig(
            authorize_url="https://provider.test/authorize",
            token_url="https://provider.test/token",
            scopes=("read", "write"),
            client_id="cid",
            client_secret="csecret",
        )

    async def push(self, *, access_token: str, request: PushRequest) -> PushResult:
        if request.external_id:
            return PushResult(external_id=request.external_id, created=False)
        return PushResult(external_id="sf-retry-ok", created=True)


def _token_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "access_token": "access-AAA",
            "refresh_token": "refresh-BBB",
            "expires_in": 3600,
            "scope": "read write",
        },
    )


@pytest.fixture
def configured_provider(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setitem(providers.REGISTRY, IntegrationProviderKind.SALESFORCE, _RetryProvider)
    monkeypatch.setattr(
        services,
        "default_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(_token_handler)),
    )
    monkeypatch.setenv("SALESFORCE_CLIENT_ID", "cid")
    monkeypatch.setenv("SALESFORCE_CLIENT_SECRET", "csecret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# DB seed helpers (write directly with a NullPool engine, loop-agnostic)
# ---------------------------------------------------------------------------


def _seed_connection(workspace_id: str, *, status: ConnectionStatus) -> str:
    new_id = uuid.uuid4()

    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            cipher = services.token_cipher(get_settings())
            conn = Connection(
                id=new_id,
                workspace_id=uuid.UUID(workspace_id),
                provider=IntegrationProviderKind.SALESFORCE,
                name="Seeded",
                status=status,
            )
            # A live (non-expired) token so retry's auto-refresh is a no-op.
            conn.access_token_encrypted = cipher.encrypt("live-access-token")
            session.add(conn)
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())
    return str(new_id)


def _seed_push_log(
    workspace_id: str,
    connection_id: str,
    *,
    status: PushStatus,
    external_id: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    idempotency_key: str | None = None,
    signal_id: str | None = "sig-1",
    request: dict[str, Any] | None = None,
) -> str:
    new_id = uuid.uuid4()

    async def _do() -> None:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            log = PushLog(
                id=new_id,
                workspace_id=uuid.UUID(workspace_id),
                connection_id=uuid.UUID(connection_id),
                target="salesforce.Opportunity",
                request=request or {"Name": "Acme"},
                signal_id=signal_id,
                status=status,
                external_id=external_id,
                error_code=PushErrorCode(error_code) if error_code else None,
                error_message=error_message,
                idempotency_key=idempotency_key,
                attempt_count=1 if status != PushStatus.PENDING else 0,
            )
            session.add(log)
            await session.commit()
        await engine.dispose()

    asyncio.run(_do())
    return str(new_id)


def _load_log(push_log_id: str) -> PushLog:
    async def _do() -> PushLog:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            from sqlalchemy import select

            row = (
                await session.execute(select(PushLog).where(PushLog.id == uuid.UUID(push_log_id)))
            ).scalar_one()
            return row

    return asyncio.run(_do())


def _count_logs(workspace_id: str) -> int:
    async def _do() -> int:
        engine = create_async_engine(_require_db(), poolclass=NullPool)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            from sqlalchemy import func, select

            n = (
                await session.execute(
                    select(func.count())
                    .select_from(PushLog)
                    .where(PushLog.workspace_id == uuid.UUID(workspace_id))
                )
            ).scalar_one()
            return int(n)

    return asyncio.run(_do())


# ---------------------------------------------------------------------------
# GET /push-log/failures — list, diagnosis, filtering, RBAC, scoping
# ---------------------------------------------------------------------------


def test_failures_requires_auth(client: TestClient) -> None:
    resp = client.get(FAILURES)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_failures_lists_only_failed_and_dead_letter(client: TestClient) -> None:
    token, _ = _signup(client, "k5-list@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"], status=ConnectionStatus.DEGRADED)
    _seed_push_log(ws["id"], conn_id, status=PushStatus.SUCCESS, external_id="ok-1")
    _seed_push_log(
        ws["id"], conn_id, status=PushStatus.FAILED, error_code="transient", error_message="503"
    )
    _seed_push_log(
        ws["id"],
        conn_id,
        status=PushStatus.DEAD_LETTER,
        error_code="validation",
        error_message="CloseDate is required",
    )

    body = client.get(FAILURES, headers=_hdr(token, ws["id"])).json()
    statuses = sorted(row["status"] for row in body["data"])
    assert statuses == ["dead_letter", "failed"]  # success excluded
    assert "next_cursor" in body


def test_failures_carry_inline_diagnosis(client: TestClient) -> None:
    token, _ = _signup(client, "k5-diag@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"], status=ConnectionStatus.NEEDS_REAUTH)
    _seed_push_log(
        ws["id"], conn_id, status=PushStatus.FAILED, error_code="auth", error_message="401"
    )

    body = client.get(FAILURES, headers=_hdr(token, ws["id"])).json()
    row = body["data"][0]
    assert row["error"]["code"] == "auth"
    diag = row["diagnosis"]
    assert diag["code"] == "auth"
    assert diag["needs_reauth"] is True
    assert diag["cause"]  # human-readable, non-empty


def test_failures_workspace_scoped(client: TestClient) -> None:
    token_a, _ = _signup(client, "k5-a@example.com")
    token_b, _ = _signup(client, "k5-b@example.com")
    ws_a = _create_workspace(client, token_a, "A")
    ws_b = _create_workspace(client, token_b, "B")
    conn_a = _seed_connection(ws_a["id"], status=ConnectionStatus.DEGRADED)
    _seed_push_log(ws_a["id"], conn_a, status=PushStatus.FAILED, error_code="transient")

    assert len(client.get(FAILURES, headers=_hdr(token_a, ws_a["id"])).json()["data"]) == 1
    # Workspace B sees none of A's failures.
    assert client.get(FAILURES, headers=_hdr(token_b, ws_b["id"])).json()["data"] == []


def test_failures_allows_member_but_forbids_viewer(client: TestClient) -> None:
    owner_token, _ = _signup(client, "k5-owner@example.com")
    ws = _create_workspace(client, owner_token)
    conn_id = _seed_connection(ws["id"], status=ConnectionStatus.DEGRADED)
    _seed_push_log(ws["id"], conn_id, status=PushStatus.FAILED, error_code="transient")

    member_token, member_id = _signup(client, "k5-member@example.com")
    _add_member_at_role(member_id, ws["id"], MembershipRole.MEMBER)
    assert client.get(FAILURES, headers=_hdr(member_token, ws["id"])).status_code == 200

    viewer_token, viewer_id = _signup(client, "k5-viewer@example.com")
    _add_member_at_role(viewer_id, ws["id"], MembershipRole.VIEWER)
    assert client.get(FAILURES, headers=_hdr(viewer_token, ws["id"])).status_code == 403


# ---------------------------------------------------------------------------
# POST /push-log/{id}/retry — idempotent retry, state guard, scoping
# ---------------------------------------------------------------------------


def test_retry_now_fixed_push_succeeds(client: TestClient, configured_provider: Any) -> None:
    token, _ = _signup(client, "k5-retry-ok@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"], status=ConnectionStatus.DEGRADED)
    log_id = _seed_push_log(
        ws["id"],
        conn_id,
        status=PushStatus.FAILED,
        error_code="transient",
        error_message="503",
        idempotency_key="stable-key",
    )

    resp = client.post(_retry_url(log_id), headers=_hdr(token, ws["id"]))
    assert resp.status_code == 200, resp.text
    push_log = resp.json()["push_log"]
    assert push_log["status"] == "success"
    assert push_log["external_id"] == "sf-retry-ok"
    # A fresh push-log row was appended; the original failed row is preserved.
    assert push_log["id"] != log_id
    assert _count_logs(ws["id"]) == 2
    assert _load_log(log_id).status == PushStatus.FAILED


def test_retry_non_failed_row_is_conflict(client: TestClient) -> None:
    token, _ = _signup(client, "k5-retry-409@example.com")
    ws = _create_workspace(client, token)
    conn_id = _seed_connection(ws["id"], status=ConnectionStatus.HEALTHY)
    log_id = _seed_push_log(ws["id"], conn_id, status=PushStatus.SUCCESS, external_id="ok")

    resp = client.post(_retry_url(log_id), headers=_hdr(token, ws["id"]))
    assert resp.status_code == 409, resp.text
    # RFC 7807 problem body carries the machine code in the ``type`` URI
    # (https://docs.civicsignals.io/errors/<code>), not a top-level ``code``.
    assert resp.json()["type"].endswith("/not_retryable")


def test_retry_missing_row_is_404(client: TestClient) -> None:
    token, _ = _signup(client, "k5-retry-404@example.com")
    ws = _create_workspace(client, token)
    resp = client.post(_retry_url(str(uuid.uuid4())), headers=_hdr(token, ws["id"]))
    assert resp.status_code == 404


def test_retry_foreign_workspace_row_is_404(client: TestClient) -> None:
    token_a, _ = _signup(client, "k5-retry-fa@example.com")
    token_b, _ = _signup(client, "k5-retry-fb@example.com")
    ws_a = _create_workspace(client, token_a, "A")
    ws_b = _create_workspace(client, token_b, "B")
    conn_a = _seed_connection(ws_a["id"], status=ConnectionStatus.DEGRADED)
    log_id = _seed_push_log(ws_a["id"], conn_a, status=PushStatus.FAILED, error_code="transient")

    # Workspace B cannot retry workspace A's push-log entry.
    resp = client.post(_retry_url(log_id), headers=_hdr(token_b, ws_b["id"]))
    assert resp.status_code == 404
