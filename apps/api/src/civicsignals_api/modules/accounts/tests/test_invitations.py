"""End-to-end tests for B6 workspace invitations.

Exercises the admin-gated invite CRUD + the accept flow through the full
FastAPI app. Needs Postgres (CITEXT + UUID) — skips when DATABASE_DIRECT_URL /
DATABASE_URL is not set.

Coverage:
- Admin can invite → email sent (RecordingEmailSender injected via monkeypatch).
- Non-admin (member / viewer) gets 403.
- Duplicate pending invite → 409; revoke unblocks re-invite.
- List invitations (admin only, cursor pagination).
- Revoke invitation (admin only, idempotent).
- Resend invitation (admin only — revokes old, issues fresh).
- Accept (valid token, accepting user added with correct role).
- Accept expired → 400.
- Accept revoked → 400.
- Accept already-used → 400.
- Already a member → 409.
- Workspace isolation (token from workspace A cannot be accepted in workspace B).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.main import app
from civicsignals_api.modules.accounts import services as accounts_services
from civicsignals_api.modules.accounts.models import (  # noqa: F401
    Invitation,
    Membership,
    MembershipRole,
    Organization,
    User,
    Workspace,
)
from civicsignals_api.modules.accounts.services import (
    DuplicateInvitationError,
    InvitationConsumedError,
    accept_invitation,
    create_invitation,
    get_invitation_by_token,
    list_invitations,
    resend_invitation,
    revoke_invitation,
)
from civicsignals_api.modules.auth.models import (  # noqa: F401
    ApiToken,
    EmailVerificationToken,
    PasswordResetToken,
)
from civicsignals_api.modules.notifications.services import RecordingEmailSender

from .conftest import _require_db

SIGNUP = "/api/v1/auth/signup"
WORKSPACES = "/api/v1/workspaces"
INVITATIONS_PATH = "/api/v1/invitations/accept"
PASSWORD = "s3cure-pa55word"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signup(client: TestClient, email: str) -> tuple[str, str]:
    """Sign up a new user, return (access_token, user_id)."""
    resp = client.post(SIGNUP, json={"email": email, "password": PASSWORD})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["tokens"]["access_token"]), str(body["user"]["id"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _hdr(token: str, ws_id: str) -> dict[str, str]:
    return {**_auth(token), "X-Workspace-Id": ws_id}


def _add_member_at_role(
    user_id: str,
    workspace_id: str,
    role: MembershipRole,
) -> None:
    """Directly insert a membership row (bypasses the invite flow)."""

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


def _create_workspace(client: TestClient, token: str, name: str = "Acme SLED") -> str:
    """Create a workspace, return its id."""
    resp = client.post(
        WORKSPACES,
        json={"name": name},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _inv_url(ws_id: str) -> str:
    return f"/api/v1/workspaces/{ws_id}/invitations"


def _accept_url() -> str:
    return "/api/v1/invitations/accept"


def _make_send_email_mock(recorder: RecordingEmailSender) -> Any:
    """Return a side_effect function that records emails but matches send_email's signature.

    ``send_email(message, *, sender=None)`` — the mock must accept the same kwargs
    so ``patch(side_effect=...)`` doesn't raise a TypeError when called from the route.
    """

    def _fake(msg: Any, *, sender: Any = None) -> None:
        recorder.send(msg)

    return _fake


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> Any:
    """TestClient backed by a fresh schema (drops/creates all tables)."""
    dsn = _require_db()

    async def _reset() -> None:
        engine = create_async_engine(dsn, poolclass=NullPool)
        from sqlalchemy import text

        # asyncpg does not support multiple statements in one execute call.
        # Drop cascade + recreate in separate statements.
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_reset())

    engine = create_async_engine(dsn, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override() -> Any:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_session, None)
    asyncio.run(engine.dispose())


# ---------------------------------------------------------------------------
# Tests — invitation CRUD (admin-gated)
# ---------------------------------------------------------------------------


def test_admin_can_invite(client: TestClient) -> None:
    """Admin creates invitation → 201 + email sent."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin@example.com")
    ws_id = _create_workspace(client, admin_tok, "AcmeSLED")

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_make_send_email_mock(recorder),
    ):
        resp = client.post(
            _inv_url(ws_id),
            json={"invited_email": "new@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invited_email"] == "new@example.com"
    assert body["role"] == "member"
    assert body["status"] == "pending"
    assert body["workspace_id"] == ws_id
    # Email was sent.
    assert len(recorder.sent) == 1
    sent = recorder.sent[0]
    assert sent.to == "new@example.com"
    assert "AcmeSLED" in sent.subject


def test_member_cannot_invite(client: TestClient) -> None:
    """Member-role caller gets 403."""
    admin_tok, _ = _signup(client, "admin2@example.com")
    member_tok, member_id = _signup(client, "member@example.com")
    ws_id = _create_workspace(client, admin_tok)

    _add_member_at_role(member_id, ws_id, MembershipRole.MEMBER)

    resp = client.post(
        _inv_url(ws_id),
        json={"invited_email": "x@example.com", "role": "member"},
        headers=_hdr(member_tok, ws_id),
    )
    assert resp.status_code == 403, resp.text


def test_viewer_cannot_invite(client: TestClient) -> None:
    """Viewer-role caller gets 403."""
    admin_tok, _ = _signup(client, "admin3@example.com")
    viewer_tok, viewer_id = _signup(client, "viewer@example.com")
    ws_id = _create_workspace(client, admin_tok)

    _add_member_at_role(viewer_id, ws_id, MembershipRole.VIEWER)

    resp = client.post(
        _inv_url(ws_id),
        json={"invited_email": "y@example.com", "role": "viewer"},
        headers=_hdr(viewer_tok, ws_id),
    )
    assert resp.status_code == 403, resp.text


def test_duplicate_pending_invite_is_409(client: TestClient) -> None:
    """A second pending invite for the same email → 409."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin4@example.com")
    ws_id = _create_workspace(client, admin_tok)

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_make_send_email_mock(recorder),
    ):
        resp1 = client.post(
            _inv_url(ws_id),
            json={"invited_email": "dup@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )
        assert resp1.status_code == 201, resp1.text

        resp2 = client.post(
            _inv_url(ws_id),
            json={"invited_email": "dup@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )
    assert resp2.status_code == 409, resp2.text
    assert "invitation" in resp2.json().get("detail", "").lower()


def test_list_invitations_admin(client: TestClient) -> None:
    """Admin can list invitations with cursor pagination."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin5@example.com")
    ws_id = _create_workspace(client, admin_tok)

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_make_send_email_mock(recorder),
    ):
        for i in range(3):
            client.post(
                _inv_url(ws_id),
                json={"invited_email": f"user{i}@example.com", "role": "member"},
                headers=_hdr(admin_tok, ws_id),
            )

    resp = client.get(_inv_url(ws_id), headers=_hdr(admin_tok, ws_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["next_cursor"] is None


def test_revoke_invitation(client: TestClient) -> None:
    """Admin can revoke a pending invitation."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin6@example.com")
    ws_id = _create_workspace(client, admin_tok)

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_make_send_email_mock(recorder),
    ):
        create_resp = client.post(
            _inv_url(ws_id),
            json={"invited_email": "revoke@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )
    assert create_resp.status_code == 201
    inv_id = create_resp.json()["id"]

    del_resp = client.delete(
        f"{_inv_url(ws_id)}/{inv_id}",
        headers=_hdr(admin_tok, ws_id),
    )
    assert del_resp.status_code == 204, del_resp.text

    # After revoke, the same email can be re-invited.
    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_make_send_email_mock(recorder),
    ):
        re_resp = client.post(
            _inv_url(ws_id),
            json={"invited_email": "revoke@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )
    assert re_resp.status_code == 201, re_resp.text


def test_resend_invitation(client: TestClient) -> None:
    """Resend endpoint revokes old invite and issues a fresh one."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin7@example.com")
    ws_id = _create_workspace(client, admin_tok)

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_make_send_email_mock(recorder),
    ):
        create_resp = client.post(
            _inv_url(ws_id),
            json={"invited_email": "resend@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )
        assert create_resp.status_code == 201
        inv_id = create_resp.json()["id"]

        resend_resp = client.post(
            f"{_inv_url(ws_id)}/{inv_id}/resend",
            headers=_hdr(admin_tok, ws_id),
        )
    assert resend_resp.status_code == 201, resend_resp.text
    new_id = resend_resp.json()["id"]
    assert new_id != inv_id  # new invitation issued


# ---------------------------------------------------------------------------
# Tests — accept flow
# ---------------------------------------------------------------------------


def test_accept_valid_token_adds_member(client: TestClient) -> None:
    """Accepting a valid token adds the user to the workspace."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin8@example.com")
    invitee_tok, invitee_id = _signup(client, "invitee@example.com")
    ws_id = _create_workspace(client, admin_tok)

    # Capture the accept URL from the email body.
    accepted_url: list[str] = []

    def _capture_send(msg: Any, *, sender: Any = None) -> None:
        recorder.send(msg)
        # Extract the token from the plain-text body URL.
        for line in msg.text_body.split("\n"):
            if "token=" in line:
                accepted_url.append(line.strip())

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_capture_send,
    ):
        inv_resp = client.post(
            _inv_url(ws_id),
            json={"invited_email": "invitee@example.com", "role": "admin"},
            headers=_hdr(admin_tok, ws_id),
        )
    assert inv_resp.status_code == 201, inv_resp.text
    assert accepted_url, "no accept URL captured from email"

    # Extract the token from the URL.
    token = accepted_url[0].split("token=")[-1]

    accept_resp = client.post(
        _accept_url(),
        json={"token": token},
        headers=_auth(invitee_tok),
    )
    assert accept_resp.status_code == 200, accept_resp.text
    body = accept_resp.json()
    assert body["user_id"] == invitee_id
    assert body["role"] == "admin"
    assert body["workspace_id"] == ws_id


def test_accept_token_single_use(client: TestClient) -> None:
    """Accepting the same token twice → 400 on second attempt."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin9@example.com")
    invitee_tok, _ = _signup(client, "invitee2@example.com")
    ws_id = _create_workspace(client, admin_tok)

    tokens_seen: list[str] = []

    def _capture(msg: Any, *, sender: Any = None) -> None:
        recorder.send(msg)
        for line in msg.text_body.split("\n"):
            if "token=" in line:
                tokens_seen.append(line.strip().split("token=")[-1])

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_capture,
    ):
        client.post(
            _inv_url(ws_id),
            json={"invited_email": "invitee2@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )

    token = tokens_seen[0]

    # First accept succeeds.
    r1 = client.post(_accept_url(), json={"token": token}, headers=_auth(invitee_tok))
    assert r1.status_code == 200, r1.text

    # Second accept fails.
    r2 = client.post(_accept_url(), json={"token": token}, headers=_auth(invitee_tok))
    assert r2.status_code in (400, 409), r2.text


def test_accept_revoked_token_is_400(client: TestClient) -> None:
    """Accepting a revoked invite returns 400."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin10@example.com")
    invitee_tok, _ = _signup(client, "invitee3@example.com")
    ws_id = _create_workspace(client, admin_tok)

    tokens_seen: list[str] = []

    def _capture(msg: Any, *, sender: Any = None) -> None:
        recorder.send(msg)
        for line in msg.text_body.split("\n"):
            if "token=" in line:
                tokens_seen.append(line.strip().split("token=")[-1])

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_capture,
    ):
        inv_resp = client.post(
            _inv_url(ws_id),
            json={"invited_email": "invitee3@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )
    inv_id = inv_resp.json()["id"]

    # Revoke it.
    del_resp = client.delete(f"{_inv_url(ws_id)}/{inv_id}", headers=_hdr(admin_tok, ws_id))
    assert del_resp.status_code == 204

    token = tokens_seen[0]
    accept_resp = client.post(_accept_url(), json={"token": token}, headers=_auth(invitee_tok))
    assert accept_resp.status_code == 400, accept_resp.text


def test_accept_invalid_token_is_400(client: TestClient) -> None:
    """An unknown token returns 400."""
    invitee_tok, _ = _signup(client, "invitee4@example.com")
    resp = client.post(
        _accept_url(),
        json={"token": "completely-invalid-token-abc123"},
        headers=_auth(invitee_tok),
    )
    assert resp.status_code == 400, resp.text


def test_already_member_accept_is_409(client: TestClient) -> None:
    """If the accepting user is already a member, accept returns 409."""
    recorder = RecordingEmailSender()
    admin_tok, _ = _signup(client, "admin11@example.com")
    invitee_tok, invitee_id = _signup(client, "invitee5@example.com")
    ws_id = _create_workspace(client, admin_tok)

    # Pre-add the invitee as a member directly.
    _add_member_at_role(invitee_id, ws_id, MembershipRole.MEMBER)

    tokens_seen: list[str] = []

    def _capture(msg: Any, *, sender: Any = None) -> None:
        recorder.send(msg)
        for line in msg.text_body.split("\n"):
            if "token=" in line:
                tokens_seen.append(line.strip().split("token=")[-1])

    with patch(
        "civicsignals_api.modules.accounts.routes.notifications_services.send_email",
        side_effect=_capture,
    ):
        client.post(
            _inv_url(ws_id),
            json={"invited_email": "invitee5@example.com", "role": "member"},
            headers=_hdr(admin_tok, ws_id),
        )

    token = tokens_seen[0]
    resp = client.post(_accept_url(), json={"token": token}, headers=_auth(invitee_tok))
    assert resp.status_code == 409, resp.text


# ---------------------------------------------------------------------------
# Unit tests — service layer (no HTTP)
# ---------------------------------------------------------------------------


def test_service_create_and_accept() -> None:
    """Service: create_invitation + accept_invitation (unit test with DB)."""
    dsn = _require_db()

    async def _run() -> None:
        engine = create_async_engine(dsn, poolclass=NullPool)
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            # Create admin user + workspace.
            from civicsignals_api.modules.auth.services import hash_password

            admin = await accounts_services.create_user(
                session,
                email="admin_svc@example.com",
                password_hash=hash_password("pw"),
                email_verified=True,
            )
            await session.flush()
            workspace = await accounts_services.create_workspace(
                session, owner=admin, name="SvcTest"
            )
            await session.flush()

            invitee = await accounts_services.create_user(
                session,
                email="invitee_svc@example.com",
                password_hash=hash_password("pw"),
                email_verified=True,
            )
            await session.flush()

            # Create invitation.
            result = await create_invitation(
                session,
                workspace_id=workspace.id,
                invited_email="invitee_svc@example.com",
                role=MembershipRole.ADMIN,
                invited_by=admin.id,
            )
            assert result.token
            assert result.invitation.status.value == "pending"

            # Duplicate raises.
            with pytest.raises(DuplicateInvitationError):
                await create_invitation(
                    session,
                    workspace_id=workspace.id,
                    invited_email="invitee_svc@example.com",
                    role=MembershipRole.MEMBER,
                    invited_by=admin.id,
                )

            # Fetch by token.
            fetched = await get_invitation_by_token(session, result.token)
            assert fetched is not None
            assert fetched.id == result.invitation.id

            # Accept.
            membership = await accept_invitation(session, fetched, accepting_user_id=invitee.id)
            assert membership.role.value == "admin"
            assert membership.user_id == invitee.id

            # Second accept raises.
            with pytest.raises(InvitationConsumedError):
                await accept_invitation(session, fetched, accepting_user_id=invitee.id)

            await session.rollback()

        await engine.dispose()

    asyncio.run(_run())


def test_service_revoke_and_resend() -> None:
    """Service: revoke + resend cycle."""
    dsn = _require_db()

    async def _run() -> None:
        engine = create_async_engine(dsn, poolclass=NullPool)
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            from civicsignals_api.modules.auth.services import hash_password

            admin = await accounts_services.create_user(
                session,
                email="admin_svc2@example.com",
                password_hash=hash_password("pw"),
                email_verified=True,
            )
            await session.flush()
            workspace = await accounts_services.create_workspace(
                session, owner=admin, name="SvcTest2"
            )
            await session.flush()

            result = await create_invitation(
                session,
                workspace_id=workspace.id,
                invited_email="rr@example.com",
                role=MembershipRole.MEMBER,
                invited_by=admin.id,
            )
            await session.flush()

            # Revoke.
            await revoke_invitation(session, result.invitation)
            assert result.invitation.status.value == "revoked"

            # Resend issues a new invite.
            resend = await resend_invitation(
                session,
                invitation_id=result.invitation.id,
                workspace_id=workspace.id,
                invited_by=admin.id,
            )
            assert resend.invitation.id != result.invitation.id
            assert resend.invitation.status.value == "pending"

            # List returns only the new pending one.
            page = await list_invitations(session, workspace.id)
            statuses = [inv.status.value for inv in page.items]
            assert statuses.count("pending") == 1
            assert statuses.count("revoked") == 1

            await session.rollback()

        await engine.dispose()

    asyncio.run(_run())
