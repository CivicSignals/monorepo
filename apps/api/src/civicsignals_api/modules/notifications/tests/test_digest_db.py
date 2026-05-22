"""DB-backed digest CRUD / selection / payload tests (H3).

Needs Postgres (ON CONFLICT upsert, partial index, JSONB feed join); the
``session_factory`` fixture skips when no DSN is configured. CI provides one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civicsignals_api.modules.accounts.models import Organization, User, Workspace
from civicsignals_api.modules.notifications import services
from civicsignals_api.modules.notifications.digest import DigestFrequency
from civicsignals_api.modules.searches.models import SavedSearch
from civicsignals_api.modules.signals.models import Signal
from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore


def _utc(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=UTC)


async def _seed_workspace(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a user + org + workspace; return ``(user_id, workspace_id)``."""
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com", name="Tester")
    session.add(user)
    await session.flush()
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    ws = Workspace(
        organization_id=org.id,
        name="Acme SLED",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        owner_id=user.id,
    )
    session.add(ws)
    await session.flush()
    return user.id, ws.id


async def _seed_saved_search(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    filters: dict[str, object] | None = None,
) -> uuid.UUID:
    search = SavedSearch(
        workspace_id=workspace_id,
        created_by=user_id,
        name="Hot RFPs",
        filters=filters or {},
    )
    session.add(search)
    await session.flush()
    return search.id


async def _seed_scored_signal(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    occurred_at: datetime,
    score: float = 80.0,
) -> uuid.UUID:
    signal = Signal(
        signal_type="rfp_posted",
        recipe_id="test-recipe",
        content_hash=uuid.uuid4().hex,
        title="Test RFP",
        summary="A test RFP signal",
        occurred_at=occurred_at,
    )
    session.add(signal)
    await session.flush()
    score_row = WorkspaceScore(
        workspace_id=workspace_id,
        signal_id=signal.id,
        score=score,
        status="new",
    )
    session.add(score_row)
    await session.flush()
    return signal.id


# ---- CRUD upsert ------------------------------------------------------------ #


async def test_upsert_creates_then_updates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user_id, ws_id = await _seed_workspace(session)
        search_id = await _seed_saved_search(session, workspace_id=ws_id, user_id=user_id)
        await session.commit()

    async with session_factory() as session:
        sub = await services.upsert_digest_subscription(
            session,
            saved_search_id=search_id,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.DAILY,
            send_hour=9,
            timezone="America/New_York",
        )
        await session.commit()
        first_id = sub.id
        assert sub.frequency == "daily"
        assert sub.send_hour == 9
        assert sub.timezone == "America/New_York"

    # Re-upsert the same (search, user) -> same row, updated fields, dedupe reset.
    async with session_factory() as session:
        sub = await services.upsert_digest_subscription(
            session,
            saved_search_id=search_id,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.WEEKLY,
            send_hour=6,
            weekday=2,
        )
        await session.commit()
        assert sub.id == first_id  # upsert, not a new row
        assert sub.frequency == "weekly"
        assert sub.weekday == 2
        assert sub.last_sent_period is None


async def test_get_digest_subscription_scoped_to_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user_id, ws_id = await _seed_workspace(session)
        search_id = await _seed_saved_search(session, workspace_id=ws_id, user_id=user_id)
        await services.upsert_digest_subscription(
            session,
            saved_search_id=search_id,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.DAILY,
        )
        await session.commit()

    async with session_factory() as session:
        mine = await services.get_digest_subscription(
            session, saved_search_id=search_id, user_id=user_id
        )
        assert mine is not None
        other = await services.get_digest_subscription(
            session, saved_search_id=search_id, user_id=uuid.uuid4()
        )
        assert other is None


# ---- selection -------------------------------------------------------------- #


async def test_list_active_excludes_off(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user_id, ws_id = await _seed_workspace(session)
        s_on = await _seed_saved_search(session, workspace_id=ws_id, user_id=user_id)
        s_off = await _seed_saved_search(session, workspace_id=ws_id, user_id=user_id)
        await services.upsert_digest_subscription(
            session,
            saved_search_id=s_on,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.DAILY,
        )
        await services.upsert_digest_subscription(
            session,
            saved_search_id=s_off,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.OFF,
        )
        await session.commit()

    async with session_factory() as session:
        active = await services.list_active_subscriptions(session)
        assert {sub.saved_search_id for sub in active} == {s_on}
        due = services.select_due_subscriptions(active, now=_utc(2026, 1, 15, 12))
        assert {sub.saved_search_id for sub in due} == {s_on}


# ---- mark_sent (dedupe claim) ---------------------------------------------- #


async def test_mark_sent_claims_period_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user_id, ws_id = await _seed_workspace(session)
        search_id = await _seed_saved_search(session, workspace_id=ws_id, user_id=user_id)
        sub = await services.upsert_digest_subscription(
            session,
            saved_search_id=search_id,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.DAILY,
            send_hour=8,
        )
        await session.commit()
        sub_id = sub.id

    now = _utc(2026, 1, 15, 8)
    async with session_factory() as session:
        first = await services.mark_sent(session, subscription_id=sub_id, now=now)
        await session.commit()
        assert first is True

    # Second claim for the same local period is a no-op (distributed-safe dedupe).
    async with session_factory() as session:
        second = await services.mark_sent(session, subscription_id=sub_id, now=now)
        await session.commit()
        assert second is False

    # A subsequent claim is no longer due for the day (already sent).
    async with session_factory() as session:
        active = await services.list_active_subscriptions(session)
        due = services.select_due_subscriptions(active, now=_utc(2026, 1, 15, 20))
        assert due == []
        # Next day it is due again.
        due_next = services.select_due_subscriptions(active, now=_utc(2026, 1, 16, 8))
        assert {sub.id for sub in due_next} == {sub_id}


# ---- payload ---------------------------------------------------------------- #


async def test_build_payload_includes_new_signals_since(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user_id, ws_id = await _seed_workspace(session)
        search_id = await _seed_saved_search(
            session,
            workspace_id=ws_id,
            user_id=user_id,
            filters={"signal_type": "rfp_posted"},
        )
        # One old signal (before the window) and one new (after).
        await _seed_scored_signal(session, workspace_id=ws_id, occurred_at=_utc(2026, 1, 10, 0))
        await _seed_scored_signal(session, workspace_id=ws_id, occurred_at=_utc(2026, 1, 14, 0))
        sub = await services.upsert_digest_subscription(
            session,
            saved_search_id=search_id,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.DAILY,
        )
        await session.commit()
        sub_id = sub.id

    async with session_factory() as session:
        fetched = await services.get_digest_subscription_by_id(session, subscription_id=sub_id)
        assert fetched is not None
        # "since" the 12th -> only the 14th signal qualifies.
        payload = await services.build_digest_payload(
            session, subscription=fetched, since=_utc(2026, 1, 12, 0)
        )
        assert payload["saved_search_name"] == "Hot RFPs"
        assert len(payload["signals"]) == 1
        assert payload["signals"][0]["signal_type"] == "rfp_posted"


async def test_build_payload_first_send_no_since_returns_all(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user_id, ws_id = await _seed_workspace(session)
        search_id = await _seed_saved_search(session, workspace_id=ws_id, user_id=user_id)
        await _seed_scored_signal(session, workspace_id=ws_id, occurred_at=_utc(2026, 1, 10, 0))
        await _seed_scored_signal(session, workspace_id=ws_id, occurred_at=_utc(2026, 1, 14, 0))
        sub = await services.upsert_digest_subscription(
            session,
            saved_search_id=search_id,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.DAILY,
        )
        await session.commit()
        sub_id = sub.id

    async with session_factory() as session:
        fetched = await services.get_digest_subscription_by_id(session, subscription_id=sub_id)
        assert fetched is not None
        payload = await services.build_digest_payload(session, subscription=fetched, since=None)
        assert len(payload["signals"]) == 2


# ---- CASCADE cleanup -------------------------------------------------------- #


async def test_deleting_saved_search_cascades_subscription(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user_id, ws_id = await _seed_workspace(session)
        search_id = await _seed_saved_search(session, workspace_id=ws_id, user_id=user_id)
        sub = await services.upsert_digest_subscription(
            session,
            saved_search_id=search_id,
            workspace_id=ws_id,
            user_id=user_id,
            frequency=DigestFrequency.DAILY,
        )
        await session.commit()
        sub_id = sub.id

    async with session_factory() as session:
        search = await session.get(SavedSearch, search_id)
        assert search is not None
        await session.delete(search)
        await session.commit()

    async with session_factory() as session:
        gone = await services.get_digest_subscription_by_id(session, subscription_id=sub_id)
        assert gone is None
