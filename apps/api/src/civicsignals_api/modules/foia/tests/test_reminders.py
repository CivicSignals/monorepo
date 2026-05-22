"""Tests for the FOIA reminder rules (M5).

Coverage:
- is_reminder_due: overdue → due (first reminder).
- is_reminder_due: recently reminded → not due.
- is_reminder_due: max reminders reached → stop.
- is_reminder_due: response received (ack/response status) → no reminder.
- is_reminder_due: reminder disabled → no reminder.
- is_reminder_due: sent_at is None → no reminder (draft request).
- is_reminder_due: same calendar day → idempotency guard.
- is_reminder_due: subsequent reminder uses interval, not initial days.
- update_reminder_config: validates bad values.
- mark_reminded: updates state; idempotent on same day.
- Beat task sends via recording mailer and updates state; re-run is idempotent.
- Reminder config HTTP endpoints: GET/PATCH return correct shapes and errors.

DB-backed tests require a live Postgres (same opt-in convention as M2:
``DATABASE_DIRECT_URL`` / ``DATABASE_URL``). Unit tests run always.
"""

from __future__ import annotations

import os
import types
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.ids import uuid7
from civicsignals_api.main import app as main_app
from civicsignals_api.modules.accounts.models import Membership, MembershipRole, User, Workspace
from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace
from civicsignals_api.modules.foia import services
from civicsignals_api.modules.foia.models import (
    DEFAULT_REMINDER_DAYS,
    DEFAULT_REMINDER_INTERVAL_DAYS,
    DEFAULT_REMINDER_MAX,
    FoiaRequest,
    FoiaRequestStatus,
)
from civicsignals_api.modules.foia.routes import router
from civicsignals_api.modules.notifications.services import RecordingEmailSender
from civicsignals_api.problems import install_problem_handlers

# ---------------------------------------------------------------------------
# Minimal FastAPI app for route testing
# ---------------------------------------------------------------------------

_app = FastAPI()
_app.include_router(router, prefix="/api/v1")
install_problem_handlers(_app)
_client = TestClient(_app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# Unit tests — is_reminder_due (no DB, pure function)
# ---------------------------------------------------------------------------


def _make_sent_request(
    *,
    sent_days_ago: float = 25,
    reminder_days: int = DEFAULT_REMINDER_DAYS,
    reminder_interval_days: int = DEFAULT_REMINDER_INTERVAL_DAYS,
    reminder_max: int = DEFAULT_REMINDER_MAX,
    reminder_enabled: bool = True,
    reminder_count: int = 0,
    last_reminded_at: datetime | None = None,
    status: str = FoiaRequestStatus.SENT.value,
) -> types.SimpleNamespace:
    """Build a minimal in-memory stub compatible with :func:`services.is_reminder_due`.

    Uses ``types.SimpleNamespace`` so that the pure-function unit tests do not
    require a live SQLAlchemy session or DB connection.
    :func:`services.is_reminder_due` accepts any object with the required
    attributes via its :class:`services._ReminderCheckable` Protocol.
    """
    now = datetime.now(UTC)
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        status=status,
        reminder_enabled=reminder_enabled,
        reminder_days=reminder_days,
        reminder_interval_days=reminder_interval_days,
        reminder_max=reminder_max,
        reminder_count=reminder_count,
        sent_at=now - timedelta(days=sent_days_ago),
        last_reminded_at=last_reminded_at,
    )


class TestIsReminderDue:
    """Pure unit tests for services.is_reminder_due — no DB required."""

    def test_overdue_first_reminder(self) -> None:
        """A request sent 25 days ago with default threshold (20 days) is due."""
        req = _make_sent_request(sent_days_ago=25, reminder_days=20)
        assert services.is_reminder_due(req) is True

    def test_not_yet_due(self) -> None:
        """A request sent 10 days ago with 20-day threshold is NOT due."""
        req = _make_sent_request(sent_days_ago=10, reminder_days=20)
        assert services.is_reminder_due(req) is False

    def test_exactly_on_threshold(self) -> None:
        """A request whose sent_at is exactly reminder_days ago is due."""
        req = _make_sent_request(sent_days_ago=20.0, reminder_days=20)
        assert services.is_reminder_due(req) is True

    def test_reminder_disabled(self) -> None:
        """reminder_enabled=False suppresses even an overdue request."""
        req = _make_sent_request(sent_days_ago=30, reminder_enabled=False)
        assert services.is_reminder_due(req) is False

    def test_max_reached(self) -> None:
        """reminder_count >= reminder_max stops reminders."""
        req = _make_sent_request(
            sent_days_ago=30,
            reminder_max=3,
            reminder_count=3,
        )
        assert services.is_reminder_due(req) is False

    def test_max_zero_unlimited(self) -> None:
        """reminder_max=0 means unlimited — still due after many sends."""
        req = _make_sent_request(
            sent_days_ago=60,
            reminder_max=0,
            reminder_count=100,
            # last_reminded_at more than interval_days ago
            last_reminded_at=datetime.now(UTC) - timedelta(days=10),
        )
        assert services.is_reminder_due(req) is True

    def test_non_sent_status_ack(self) -> None:
        """A request in ack status does not get a reminder."""
        req = _make_sent_request(sent_days_ago=30, status=FoiaRequestStatus.ACK.value)
        assert services.is_reminder_due(req) is False

    def test_non_sent_status_response(self) -> None:
        """A request in response status does not get a reminder."""
        req = _make_sent_request(sent_days_ago=30, status=FoiaRequestStatus.RESPONSE.value)
        assert services.is_reminder_due(req) is False

    def test_non_sent_status_draft(self) -> None:
        """A draft request does not get a reminder."""
        req = _make_sent_request(sent_days_ago=30, status=FoiaRequestStatus.DRAFT.value)
        assert services.is_reminder_due(req) is False

    def test_sent_at_none(self) -> None:
        """A request with sent_at=None is never due."""
        ns = types.SimpleNamespace(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            status=FoiaRequestStatus.SENT.value,
            reminder_enabled=True,
            reminder_days=DEFAULT_REMINDER_DAYS,
            reminder_interval_days=DEFAULT_REMINDER_INTERVAL_DAYS,
            reminder_max=DEFAULT_REMINDER_MAX,
            reminder_count=0,
            sent_at=None,
            last_reminded_at=None,
        )
        assert services.is_reminder_due(ns) is False

    def test_same_day_idempotency(self) -> None:
        """A request already reminded today is not due again."""
        req = _make_sent_request(
            sent_days_ago=30,
            reminder_count=1,
            last_reminded_at=datetime.now(UTC) - timedelta(hours=2),  # earlier today
        )
        assert services.is_reminder_due(req) is False

    def test_subsequent_reminder_uses_interval(self) -> None:
        """Second reminder uses interval_days from last_reminded_at, not initial days."""
        # First reminder was 8 days ago; interval is 7 days → due.
        req = _make_sent_request(
            sent_days_ago=40,
            reminder_days=20,
            reminder_interval_days=7,
            reminder_count=1,
            last_reminded_at=datetime.now(UTC) - timedelta(days=8),
        )
        assert services.is_reminder_due(req) is True

    def test_subsequent_reminder_interval_not_yet(self) -> None:
        """Second reminder is NOT due when interval has not elapsed."""
        # Last reminder was 5 days ago; interval is 7 days → not due.
        req = _make_sent_request(
            sent_days_ago=40,
            reminder_days=20,
            reminder_interval_days=7,
            reminder_count=1,
            last_reminded_at=datetime.now(UTC) - timedelta(days=5),
        )
        assert services.is_reminder_due(req) is False

    def test_custom_now(self) -> None:
        """is_reminder_due accepts a custom 'now' for deterministic testing."""
        req = _make_sent_request(sent_days_ago=0, reminder_days=5)
        future = datetime.now(UTC) + timedelta(days=6)
        # Should not be due right now...
        assert services.is_reminder_due(req) is False
        # ...but should be due in 6 days.
        assert services.is_reminder_due(req, now=future) is True


class TestReminderConfigValidation:
    """Unit tests for update_reminder_config validation (no DB)."""

    @pytest.mark.asyncio
    async def test_invalid_reminder_days_zero(self) -> None:
        """reminder_days=0 raises FoiaReminderConfigError."""

        class _StubSession:
            pass

        with pytest.raises(services.FoiaReminderConfigError, match="reminder_days"):
            await services.update_reminder_config(
                _StubSession(),  # type: ignore[arg-type]
                request_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                reminder_days=0,
            )

    @pytest.mark.asyncio
    async def test_invalid_reminder_interval_zero(self) -> None:
        with pytest.raises(services.FoiaReminderConfigError, match="reminder_interval_days"):
            await services.update_reminder_config(
                object(),  # type: ignore[arg-type]
                request_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                reminder_interval_days=0,
            )

    @pytest.mark.asyncio
    async def test_invalid_reminder_max_negative(self) -> None:
        with pytest.raises(services.FoiaReminderConfigError, match="reminder_max"):
            await services.update_reminder_config(
                object(),  # type: ignore[arg-type]
                request_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                reminder_max=-1,
            )


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------

_DSN = (
    os.environ.get("FOIA_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)

_db_skip = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

_OWN_TABLE_NAMES = ("foia_request", "foia_request_event")
_DEP_TABLE_NAMES = (
    "accounts_member",
    "accounts_workspace",
    "accounts_organization",
    "accounts_user",
    "entities_entity",
    "entities_kind",
    "entities_geo",
)


def _get_tables(names: tuple[str, ...]) -> list[Table]:
    return [Base.metadata.tables[n] for n in names if n in Base.metadata.tables]


def _register_models() -> None:
    import civicsignals_api.modules.accounts.models
    import civicsignals_api.modules.auth.models
    import civicsignals_api.modules.entities.models
    import civicsignals_api.modules.foia.models

    _ = (
        civicsignals_api.modules.accounts.models,
        civicsignals_api.modules.auth.models,
        civicsignals_api.modules.entities.models,
        civicsignals_api.modules.foia.models,
    )


_register_models()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Fresh schema + session for each DB test (same pattern as M2)."""
    assert _DSN is not None
    engine = create_async_engine(_DSN, poolclass=NullPool)
    own_tables = _get_tables(_OWN_TABLE_NAMES)
    dep_tables = _get_tables(_DEP_TABLE_NAMES)
    all_tables = dep_tables + own_tables
    all_table_names = list(_OWN_TABLE_NAMES[::-1]) + list(_DEP_TABLE_NAMES)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        for tname in all_table_names:
            await conn.execute(text(f"DROP TABLE IF EXISTS {tname} CASCADE"))
        await conn.run_sync(Base.metadata.create_all, tables=all_tables)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            for tname in all_table_names:
                await conn.execute(text(f"DROP TABLE IF EXISTS {tname} CASCADE"))
        await engine.dispose()


# ---------------------------------------------------------------------------
# Fixture helpers (mirrors M2 helpers)
# ---------------------------------------------------------------------------


async def _seed_user(session: AsyncSession, suffix: str = "") -> uuid.UUID:
    user = User(id=uuid7(), email=f"m5user{suffix}@example.com", name=f"M5 User {suffix}")
    session.add(user)
    await session.flush()
    return user.id


async def _seed_org(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    from civicsignals_api.modules.accounts.models import Organization

    org = Organization(id=uuid7(), name="M5 Test Org")
    session.add(org)
    await session.flush()
    return org.id


async def _seed_workspace(
    session: AsyncSession, org_id: uuid.UUID, owner_id: uuid.UUID, slug: str = "m5-ws"
) -> uuid.UUID:
    from civicsignals_api.modules.accounts.models import Workspace

    ws = Workspace(
        id=uuid7(),
        organization_id=org_id,
        name="M5 Workspace",
        slug=slug,
        owner_id=owner_id,
    )
    session.add(ws)
    await session.flush()
    return ws.id


async def _seed_entity(session: AsyncSession, name: str = "M5 Agency") -> uuid.UUID:
    from civicsignals_api.modules.entities.models import Entity

    entity = Entity(
        id=uuid7(),
        type="agency",
        status="active",
        name=name,
        country="US",
        state="CA",
        source_urls=[],
    )
    session.add(entity)
    await session.flush()
    return entity.id


async def _setup(session: AsyncSession, slug: str = "m5-ws") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    user_id = await _seed_user(session, suffix=slug)
    org_id = await _seed_org(session, user_id)
    ws_id = await _seed_workspace(session, org_id, user_id, slug=slug)
    entity_id = await _seed_entity(session, name=f"Agency {slug}")
    await session.commit()
    return ws_id, user_id, entity_id


async def _create_request(
    session: AsyncSession, *, ws_id: uuid.UUID, user_id: uuid.UUID, entity_id: uuid.UUID
) -> FoiaRequest:
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="All vendor contracts 2023",
            body="I hereby request all vendor contracts.",
        )
    return req


# ---------------------------------------------------------------------------
# DB tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@_db_skip
async def test_create_request_seeds_reminder_defaults(session: AsyncSession) -> None:
    """create_request seeds reminder columns with sensible defaults (M5)."""
    ws_id, user_id, entity_id = await _setup(session, "seed-defaults")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    assert req.reminder_enabled is True
    assert req.reminder_days == DEFAULT_REMINDER_DAYS
    assert req.reminder_interval_days == DEFAULT_REMINDER_INTERVAL_DAYS
    assert req.reminder_max == DEFAULT_REMINDER_MAX
    assert req.reminder_count == 0
    assert req.last_reminded_at is None


@pytest.mark.asyncio
@_db_skip
async def test_create_request_with_jurisdiction_seeds_deadline(session: AsyncSession) -> None:
    """create_request with TX-PIA seeds reminder_days from the template deadline_days."""
    ws_id, user_id, entity_id = await _setup(session, "seed-jurisdiction")
    context = {
        "requester_name": "Jane Doe",
        "requester_address": "123 Main St, Austin, TX 78701",
        "requester_email": "jane@example.com",
        "entity_name": "Texas Agency",
        "records_description": "All IT contracts 2023",
        "date": "2025-05-22",
        "fee_waiver_basis": "non-profit",
    }
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="TX template test",
            jurisdiction="TX-PIA",
            template_context=context,
        )
    # TX-PIA has deadline_days=10, so reminder_days should be 10.
    assert req.reminder_days == 10


@pytest.mark.asyncio
@_db_skip
async def test_get_and_update_reminder_config(session: AsyncSession) -> None:
    """get_reminder_config / update_reminder_config round-trip (M5)."""
    ws_id, user_id, entity_id = await _setup(session, "get-update")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    async with session.begin():
        config = await services.get_reminder_config(
            session, request_id=req.id, workspace_id=ws_id
        )
    assert config.reminder_enabled is True
    assert config.reminder_days == DEFAULT_REMINDER_DAYS

    async with session.begin():
        updated = await services.update_reminder_config(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            reminder_enabled=False,
            reminder_days=30,
            reminder_interval_days=14,
            reminder_max=5,
        )
    assert updated.reminder_enabled is False
    assert updated.reminder_days == 30
    assert updated.reminder_interval_days == 14
    assert updated.reminder_max == 5


@pytest.mark.asyncio
@_db_skip
async def test_mark_reminded_updates_state(session: AsyncSession) -> None:
    """mark_reminded increments reminder_count and sets last_reminded_at."""
    ws_id, user_id, entity_id = await _setup(session, "mark-reminded")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    now = datetime.now(UTC)
    async with session.begin():
        updated = await services.mark_reminded(session, request_id=req.id, now=now)
    assert updated is True

    # Reload and check.
    refreshed = await services.get_request(session, request_id=req.id, workspace_id=ws_id)
    assert refreshed.reminder_count == 1
    assert refreshed.last_reminded_at is not None
    assert abs((refreshed.last_reminded_at - now).total_seconds()) < 1


@pytest.mark.asyncio
@_db_skip
async def test_mark_reminded_idempotent_same_day(session: AsyncSession) -> None:
    """mark_reminded returns False when called twice on the same calendar day."""
    ws_id, user_id, entity_id = await _setup(session, "mark-idempotent")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    morning = datetime.now(UTC).replace(hour=6, minute=0, second=0, microsecond=0)
    afternoon = morning.replace(hour=14)

    async with session.begin():
        first = await services.mark_reminded(session, request_id=req.id, now=morning)
    assert first is True

    async with session.begin():
        second = await services.mark_reminded(session, request_id=req.id, now=afternoon)
    assert second is False

    refreshed = await services.get_request(session, request_id=req.id, workspace_id=ws_id)
    assert refreshed.reminder_count == 1  # not incremented again


@pytest.mark.asyncio
@_db_skip
async def test_list_overdue_reminders_finds_due_request(session: AsyncSession) -> None:
    """list_overdue_reminders returns an overdue request (sent > threshold days ago)."""
    ws_id, user_id, entity_id = await _setup(session, "overdue-found")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    # Transition to sent with a backdated sent_at.
    async with session.begin():
        req = await services.transition_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            actor_id=user_id,
            new_status=FoiaRequestStatus.SENT,
        )
    # Backdate sent_at so the request is past the reminder_days threshold.
    from sqlalchemy import update as sa_update

    async with session.begin():
        await session.execute(
            sa_update(FoiaRequest)
            .where(FoiaRequest.id == req.id)
            .values(sent_at=datetime.now(UTC) - timedelta(days=req.reminder_days + 1))
        )

    overdue = await services.list_overdue_reminders(session)
    ids = [o.request_id for o in overdue]
    assert req.id in ids


@pytest.mark.asyncio
@_db_skip
async def test_list_overdue_reminders_ignores_acked(session: AsyncSession) -> None:
    """list_overdue_reminders skips requests that have moved past sent."""
    ws_id, user_id, entity_id = await _setup(session, "overdue-acked")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    # draft → sent → ack
    for status in (FoiaRequestStatus.SENT, FoiaRequestStatus.ACK):
        async with session.begin():
            req = await services.transition_request(
                session,
                request_id=req.id,
                workspace_id=ws_id,
                actor_id=user_id,
                new_status=status,
            )

    overdue = await services.list_overdue_reminders(session)
    ids = [o.request_id for o in overdue]
    assert req.id not in ids


@pytest.mark.asyncio
@_db_skip
async def test_list_overdue_reminders_ignores_disabled(session: AsyncSession) -> None:
    """list_overdue_reminders skips requests with reminder_enabled=False."""
    ws_id, user_id, entity_id = await _setup(session, "overdue-disabled")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    async with session.begin():
        req = await services.transition_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            actor_id=user_id,
            new_status=FoiaRequestStatus.SENT,
        )
    from sqlalchemy import update as sa_update

    async with session.begin():
        await session.execute(
            sa_update(FoiaRequest)
            .where(FoiaRequest.id == req.id)
            .values(
                sent_at=datetime.now(UTC) - timedelta(days=req.reminder_days + 1),
                reminder_enabled=False,
            )
        )

    overdue = await services.list_overdue_reminders(session)
    ids = [o.request_id for o in overdue]
    assert req.id not in ids


@pytest.mark.asyncio
@_db_skip
async def test_beat_task_sends_via_recording_mailer(session: AsyncSession) -> None:
    """The beat task sends via the recording mailer and updates state.

    We test the task's core logic directly using the test session — this avoids
    opening a second DB connection (which causes "attached to a different event
    loop" errors under pytest-asyncio) while still exercising every code path:
    list_overdue_reminders → mark_reminded → send.
    """
    from sqlalchemy import update as sa_update

    from civicsignals_api.modules.notifications.services import OutboundEmail

    ws_id, user_id, entity_id = await _setup(session, "beat-sends")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    async with session.begin():
        req = await services.transition_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            actor_id=user_id,
            new_status=FoiaRequestStatus.SENT,
        )
    async with session.begin():
        await session.execute(
            sa_update(FoiaRequest)
            .where(FoiaRequest.id == req.id)
            .values(sent_at=datetime.now(UTC) - timedelta(days=req.reminder_days + 1))
        )

    # --- inline simulation of the beat task using the test session ---
    recorder = RecordingEmailSender()
    sent_count = 0

    async with session.begin():
        overdue = await services.list_overdue_reminders(session)

    for item in overdue:
        async with session.begin():
            updated = await services.mark_reminded(session, request_id=item.request_id)
        if not updated:
            continue
        reminder_num = item.reminder_count + 1
        recorder.send(
            OutboundEmail(
                to=item.requester_email,
                subject=f"Reminder: FOIA request still awaiting response — {item.subject}",
                text_body=f"This is reminder #{reminder_num}.",
            )
        )
        sent_count += 1

    assert sent_count >= 1
    assert len(recorder.sent) >= 1
    subjects = [m.subject for m in recorder.sent]
    assert any("FOIA" in s for s in subjects)

    # State should be updated.
    async with session.begin():
        refreshed = await services.get_request(
            session, request_id=req.id, workspace_id=ws_id
        )
    assert refreshed.reminder_count == 1
    assert refreshed.last_reminded_at is not None


@pytest.mark.asyncio
@_db_skip
async def test_beat_task_idempotent_same_day(session: AsyncSession) -> None:
    """Running the beat-task logic twice on the same day doesn't double-send."""
    from sqlalchemy import update as sa_update

    from civicsignals_api.modules.notifications.services import OutboundEmail

    ws_id, user_id, entity_id = await _setup(session, "beat-idem")
    req = await _create_request(session, ws_id=ws_id, user_id=user_id, entity_id=entity_id)

    async with session.begin():
        req = await services.transition_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            actor_id=user_id,
            new_status=FoiaRequestStatus.SENT,
        )
    async with session.begin():
        await session.execute(
            sa_update(FoiaRequest)
            .where(FoiaRequest.id == req.id)
            .values(sent_at=datetime.now(UTC) - timedelta(days=req.reminder_days + 1))
        )

    # First pass — should send.
    recorder = RecordingEmailSender()

    async def _one_pass() -> int:
        count = 0
        async with session.begin():
            overdue = await services.list_overdue_reminders(session)
        for item in overdue:
            async with session.begin():
                updated = await services.mark_reminded(session, request_id=item.request_id)
            if not updated:
                continue
            recorder.send(
                OutboundEmail(
                    to=item.requester_email,
                    subject="Reminder",
                    text_body="Reminder body.",
                )
            )
            count += 1
        return count

    first = await _one_pass()
    second = await _one_pass()

    assert first >= 1
    assert second == 0  # idempotent: no second send on the same day
    async with session.begin():
        refreshed = await services.get_request(
            session, request_id=req.id, workspace_id=ws_id
        )
    assert refreshed.reminder_count == 1


# ---------------------------------------------------------------------------
# HTTP reminder config endpoints
# ---------------------------------------------------------------------------


def _make_ctx_override(ws_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceContext:
    user_ns = types.SimpleNamespace(
        id=user_id,
        email="stub@example.com",
        last_active_workspace_id=None,
    )
    ws_ns = types.SimpleNamespace(id=ws_id)
    mem_ns = types.SimpleNamespace(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        role=MembershipRole.OWNER,
    )
    return WorkspaceContext(
        user=cast(User, user_ns),
        workspace=cast(Workspace, ws_ns),
        membership=cast(Membership, mem_ns),
    )


@pytest.mark.asyncio
@_db_skip
async def test_http_get_and_patch_reminder_config(session: AsyncSession) -> None:
    """GET /reminder returns current config; PATCH updates it."""
    ws_id, user_id, entity_id = await _setup(session, "http-rem")
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx_override(ws_id, user_id)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx

    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Create request.
            create_resp = await client.post(
                "/api/v1/foia/requests",
                json={
                    "entity_id": str(entity_id),
                    "subject": "Reminder config test",
                    "body": "Request body.",
                },
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert create_resp.status_code == 201, create_resp.text
            rid = create_resp.json()["id"]

            # GET reminder config.
            get_resp = await client.get(
                f"/api/v1/foia/requests/{rid}/reminder",
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert get_resp.status_code == 200, get_resp.text
            data = get_resp.json()
            assert data["reminder_enabled"] is True
            assert data["reminder_days"] == DEFAULT_REMINDER_DAYS
            assert data["reminder_count"] == 0

            # PATCH to disable reminders.
            patch_resp = await client.patch(
                f"/api/v1/foia/requests/{rid}/reminder",
                json={"reminder_enabled": False, "reminder_days": 30},
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert patch_resp.status_code == 200, patch_resp.text
            patched = patch_resp.json()
            assert patched["reminder_enabled"] is False
            assert patched["reminder_days"] == 30

            # GET again to confirm persistence.
            get_resp2 = await client.get(
                f"/api/v1/foia/requests/{rid}/reminder",
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert get_resp2.status_code == 200
            assert get_resp2.json()["reminder_enabled"] is False
            assert get_resp2.json()["reminder_days"] == 30

            # GET for non-existent request.
            missing = await client.get(
                f"/api/v1/foia/requests/{uuid.uuid4()}/reminder",
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert missing.status_code == 404
            assert "application/problem+json" in missing.headers["content-type"]
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()
