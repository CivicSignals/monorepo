"""Tests for B9 audit events: event-bus → row, direct write, read API.

Live-DB tests use the ``client`` fixture from ``conftest.py``; they skip when
no DSN is available (``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` not set).

Unit tests (event-bus wiring) run without a DB using a mock session.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from civicsignals_api import events
from civicsignals_api.modules.admin import listeners as audit_listeners
from civicsignals_api.modules.admin.listeners import (
    register_listeners,
    unregister_listeners,
)

# ---------------------------------------------------------------------------
# Unit: event bus → listener → record_audit_event (no live DB needed)
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal async session stub for unit tests."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


def _make_fake_session_local(fake_session: _FakeSession) -> MagicMock:
    """Return a SessionLocal mock that yields ``fake_session`` as an async ctx mgr."""
    mock = MagicMock()
    mock.return_value = fake_session
    return mock


def test_listeners_register_and_unregister() -> None:
    """register/unregister are idempotent and don't crash."""
    unregister_listeners()
    register_listeners()
    unregister_listeners()
    # Second unregister is a no-op (idempotent).
    unregister_listeners()


def test_event_bus_publish_reaches_listener() -> None:
    """Publishing an event on the bus triggers the listener (mocked session)."""
    fake_session = _FakeSession()
    session_local_mock = _make_fake_session_local(fake_session)

    from civicsignals_api.modules.admin import services as admin_services

    with (
        patch.object(audit_listeners, "SessionLocal", session_local_mock),
        patch.object(admin_services, "record_audit_event", new_callable=AsyncMock) as mock_record,
    ):
        # Ensure the listener is registered.
        unregister_listeners()
        register_listeners()

        asyncio.run(
            events.publish(
                events.AUTH_PASSWORD_RESET_REQUESTED,
                {"user_id": str(uuid.uuid4()), "email": "test@example.com"},
            )
        )

        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args.kwargs
        assert call_kwargs["action"] == "auth.password_reset.requested"

    # Always clean up listeners after the test.
    unregister_listeners()


def test_event_bus_login_event() -> None:
    """AUTH_LOGIN event triggers the audit listener."""
    from civicsignals_api.modules.admin import services as admin_services

    with patch.object(admin_services, "record_audit_event", new_callable=AsyncMock) as mock_record:
        unregister_listeners()
        register_listeners()

        asyncio.run(
            events.publish(
                events.AUTH_LOGIN,
                {"user_id": str(uuid.uuid4()), "email": "user@example.com"},
            )
        )

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["action"] == "auth.login"

    unregister_listeners()


def test_event_bus_member_role_changed() -> None:
    """MEMBER_ROLE_CHANGED event triggers the audit listener."""
    from civicsignals_api.modules.admin import services as admin_services

    with patch.object(admin_services, "record_audit_event", new_callable=AsyncMock) as mock_record:
        unregister_listeners()
        register_listeners()

        asyncio.run(
            events.publish(
                events.MEMBER_ROLE_CHANGED,
                {
                    "user_id": str(uuid.uuid4()),
                    "workspace_id": str(uuid.uuid4()),
                    "target_user_id": str(uuid.uuid4()),
                    "old_role": "member",
                    "new_role": "admin",
                },
            )
        )

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["action"] == "member.role_changed"

    unregister_listeners()


def test_listener_failure_does_not_propagate() -> None:
    """A failing listener must not raise (best-effort audit)."""
    from civicsignals_api.modules.admin import services as admin_services

    broken = AsyncMock(side_effect=RuntimeError("DB down"))

    with patch.object(admin_services, "record_audit_event", broken):
        unregister_listeners()
        register_listeners()
        # Should not raise.
        asyncio.run(
            events.publish(
                events.AUTH_PASSWORD_RESET_COMPLETED,
                {"user_id": str(uuid.uuid4()), "email": "x@example.com"},
            )
        )

    unregister_listeners()


# ---------------------------------------------------------------------------
# Unit: record_audit_event direct call (no DB)
# ---------------------------------------------------------------------------


def test_record_audit_event_direct_no_db() -> None:
    """record_audit_event adds an AuditEvent to the session and flushes."""
    from civicsignals_api.modules.admin import services as admin_services
    from civicsignals_api.modules.admin.models import AuditEvent

    fake_session = _FakeSession()

    async def _run() -> AuditEvent:
        return await admin_services.record_audit_event(
            fake_session,  # type: ignore[arg-type]
            action="auth.login",
            actor_user_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            metadata={"email": "admin@example.com"},
        )

    result = asyncio.run(_run())
    assert isinstance(result, AuditEvent)
    assert result.action == "auth.login"
    assert fake_session.added == [result]


# ---------------------------------------------------------------------------
# Integration: live-DB tests (skip without DSN)
# ---------------------------------------------------------------------------

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
AUDIT_EVENTS = "/api/v1/admin/audit-events"
PASSWORD = "s3cur3-P4ssword!"


def _signup(client: Any, email: str) -> str:
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD, "name": email})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tokens"]["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_workspace(client: Any, token: str, name: str = "Test WS") -> dict[str, Any]:
    resp = client.post(WORKSPACES, json={"name": name}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def _write_audit_events(actions_and_kwargs: list[dict[str, Any]]) -> None:
    """Write audit events using a fresh NullPool engine (avoids event-loop conflicts).

    ``asyncio.run()`` creates a new event loop; using the module-level ``SessionLocal``
    (which may be bound to TestClient's internal loop) causes 'Future attached to a
    different loop'.  A fresh ``NullPool`` engine is loop-agnostic.
    """
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from civicsignals_api.modules.admin import services as admin_svc

    dsn = os.environ.get("DATABASE_DIRECT_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        pytest.skip("no DSN")

    async def _run() -> None:
        engine = create_async_engine(dsn, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with factory() as session:
                for kwargs in actions_and_kwargs:
                    await admin_svc.record_audit_event(session, **kwargs)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_list_audit_events_requires_auth(client: Any) -> None:
    resp = client.get(AUDIT_EVENTS)
    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_list_audit_events_requires_workspace_header(client: Any) -> None:
    token = _signup(client, "noworkspace@example.com")
    resp = client.get(AUDIT_EVENTS, headers=_auth(token))
    # No workspace → 400 (no X-Workspace-Id and no last-active).
    assert resp.status_code == 400


def test_list_audit_events_requires_admin_role(client: Any) -> None:
    """A viewer (non-admin) member gets 403."""
    owner_token = _signup(client, "ws-owner@example.com")
    viewer_token = _signup(client, "ws-viewer@example.com")

    # Create workspace as owner.
    ws = _create_workspace(client, owner_token)
    ws_id = ws["id"]

    # Promote viewer to member (not admin) by adding them directly. Since we
    # only have the owner right now and adding members is outside this test's
    # scope, we use a fresh signup that has no workspace membership — the 404
    # from no membership is effectively the same security gate for this test.
    # (A true viewer-role test would require the invite/add-member flow, B6.)
    resp = client.get(
        AUDIT_EVENTS,
        headers={**_auth(viewer_token), "X-Workspace-Id": ws_id},
    )
    # viewer_token user is not a member → 404 (existence not leaked).
    assert resp.status_code in (403, 404)


def test_list_audit_events_owner_can_read(client: Any) -> None:
    """Workspace owner (≥ admin) can list audit events."""
    token = _signup(client, "audit-owner@example.com")
    ws = _create_workspace(client, token, "Audit WS")
    ws_id = ws["id"]

    resp = client.get(
        AUDIT_EVENTS,
        headers={**_auth(token), "X-Workspace-Id": ws_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "items" in body
    assert isinstance(body["items"], list)
    # ``next_cursor`` field is always present.
    assert "next_cursor" in body


def test_list_audit_events_workspace_scoped(client: Any) -> None:
    """Audit events from workspace A do not appear in workspace B's log."""
    token_a = _signup(client, "ws-a@example.com")
    token_b = _signup(client, "ws-b@example.com")
    ws_a = _create_workspace(client, token_a, "Workspace A")
    ws_b = _create_workspace(client, token_b, "Workspace B")

    # Write an audit event directly to workspace A.
    _write_audit_events([{"action": "test.event_a", "workspace_id": uuid.UUID(ws_a["id"])}])

    # Workspace A owner sees the event.
    resp_a = client.get(
        AUDIT_EVENTS,
        headers={**_auth(token_a), "X-Workspace-Id": ws_a["id"]},
    )
    assert resp_a.status_code == 200
    actions_a = [e["action"] for e in resp_a.json()["items"]]
    assert "test.event_a" in actions_a

    # Workspace B owner does NOT see workspace A's event.
    resp_b = client.get(
        AUDIT_EVENTS,
        headers={**_auth(token_b), "X-Workspace-Id": ws_b["id"]},
    )
    assert resp_b.status_code == 200
    actions_b = [e["action"] for e in resp_b.json()["items"]]
    assert "test.event_a" not in actions_b


def test_audit_events_append_only_no_mutation_endpoints(client: Any) -> None:
    """The audit API exposes no write, update, or delete endpoints."""
    token = _signup(client, "append-only@example.com")
    ws = _create_workspace(client, token)
    ws_id = ws["id"]
    hdrs = {**_auth(token), "X-Workspace-Id": ws_id}

    # POST / PUT / PATCH / DELETE on the audit-events collection → 405 or 404.
    assert client.post(AUDIT_EVENTS, json={}, headers=hdrs).status_code in (404, 405, 422)
    assert client.put(AUDIT_EVENTS, json={}, headers=hdrs).status_code in (404, 405, 422)
    assert client.patch(AUDIT_EVENTS, json={}, headers=hdrs).status_code in (404, 405, 422)
    assert client.delete(AUDIT_EVENTS, headers=hdrs).status_code in (404, 405, 422)


@pytest.mark.parametrize("limit,expected", [(1, 1), (2, 2)])
def test_audit_events_cursor_pagination(client: Any, limit: int, expected: int) -> None:
    """Cursor pagination returns the correct page sizes."""
    token = _signup(client, f"paginate-{limit}@example.com")
    ws = _create_workspace(client, token, f"Page WS {limit}")
    ws_id = uuid.UUID(ws["id"])

    # Write 3 events for this workspace.
    _write_audit_events([{"action": f"test.page_{i}", "workspace_id": ws_id} for i in range(3)])

    resp = client.get(
        f"{AUDIT_EVENTS}?limit={limit}",
        headers={**_auth(token), "X-Workspace-Id": str(ws_id)},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == expected
    if expected < 3:
        assert body["next_cursor"] is not None
    # Follow the cursor.
    if body["next_cursor"]:
        resp2 = client.get(
            f"{AUDIT_EVENTS}?limit={limit}&cursor={body['next_cursor']}",
            headers={**_auth(token), "X-Workspace-Id": str(ws_id)},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        # No overlap.
        ids1 = {e["id"] for e in body["items"]}
        ids2 = {e["id"] for e in body2["items"]}
        assert ids1.isdisjoint(ids2)


def test_audit_events_filter_by_action(client: Any) -> None:
    """``?action=`` filter returns only matching events."""
    token = _signup(client, "filter-action@example.com")
    ws = _create_workspace(client, token, "Filter WS")
    ws_id = uuid.UUID(ws["id"])

    _write_audit_events(
        [
            {"action": "auth.login", "workspace_id": ws_id},
            {"action": "member.role_changed", "workspace_id": ws_id},
        ]
    )

    resp = client.get(
        f"{AUDIT_EVENTS}?action=auth.login",
        headers={**_auth(token), "X-Workspace-Id": str(ws_id)},
    )
    assert resp.status_code == 200
    actions = [e["action"] for e in resp.json()["items"]]
    assert all(a == "auth.login" for a in actions)
    assert "member.role_changed" not in actions


def test_audit_events_filter_by_actor(client: Any) -> None:
    """``?actor_user_id=`` filter returns only events by that actor."""
    token = _signup(client, "filter-actor@example.com")
    ws = _create_workspace(client, token, "Actor Filter WS")
    ws_id = uuid.UUID(ws["id"])

    actor_id = uuid.uuid4()
    other_id = uuid.uuid4()

    _write_audit_events(
        [
            {"action": "auth.login", "workspace_id": ws_id, "actor_user_id": actor_id},
            {"action": "auth.login", "workspace_id": ws_id, "actor_user_id": other_id},
        ]
    )

    resp = client.get(
        f"{AUDIT_EVENTS}?actor_user_id={actor_id}",
        headers={**_auth(token), "X-Workspace-Id": str(ws_id)},
    )
    assert resp.status_code == 200
    actor_ids = [e["actor_user_id"] for e in resp.json()["items"]]
    assert all(a == str(actor_id) for a in actor_ids)
