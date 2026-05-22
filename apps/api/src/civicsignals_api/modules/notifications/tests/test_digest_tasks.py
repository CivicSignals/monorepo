"""Beat-task tests for the digest scheduler (H3) with a fake clock + fake lock.

``dispatch_digests`` (the hourly sweep) and ``send_digest`` (per-subscription
delivery) are exercised with: a patched ``SessionLocal`` pointing at the test DB,
a fake leader lock, a fake ``now`` (so daily/weekly + send-hour are deterministic),
and a captured ``send_digest.delay`` so no real Celery broker is needed.

The tests are ``async`` so pytest-asyncio (auto mode) runs them in one event loop
together with the DB fixtures; the task *cores* (``_dispatch_digests_async`` /
``_send_digest_async``) are awaited directly rather than going through the sync
Celery wrappers' ``asyncio.run`` (which would nest event loops).

Needs Postgres; skips when no DSN is configured (CI provides one).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from civicsignals_api.modules.accounts.models import Organization, User, Workspace
from civicsignals_api.modules.notifications import services, tasks
from civicsignals_api.modules.notifications.digest import DigestFrequency
from civicsignals_api.modules.searches.models import SavedSearch


def _utc(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=UTC)


class _AlwaysLock:
    """A fake RedisLock that always acquires (single-process test)."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def acquire(self) -> bool:
        return True

    def release(self) -> bool:
        return True


class _NeverLock(_AlwaysLock):
    def acquire(self) -> bool:
        return False


async def _seed(
    session: AsyncSession, *, frequency: DigestFrequency, send_hour: int = 8
) -> uuid.UUID:
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com", name="T")
    session.add(user)
    await session.flush()
    org = Organization(name="Acme")
    session.add(org)
    await session.flush()
    ws = Workspace(
        organization_id=org.id,
        name="Acme",
        slug=f"acme-{uuid.uuid4().hex[:8]}",
        owner_id=user.id,
    )
    session.add(ws)
    await session.flush()
    search = SavedSearch(workspace_id=ws.id, created_by=user.id, name="Hot RFPs", filters={})
    session.add(search)
    await session.flush()
    sub = await services.upsert_digest_subscription(
        session,
        saved_search_id=search.id,
        workspace_id=ws.id,
        user_id=user.id,
        frequency=frequency,
        send_hour=send_hour,
    )
    await session.commit()
    return sub.id


async def _seed_via(
    session_factory: async_sessionmaker[AsyncSession],
    frequency: DigestFrequency,
    *,
    send_hour: int,
) -> uuid.UUID:
    async with session_factory() as session:
        return await _seed(session, frequency=frequency, send_hour=send_hour)


async def test_dispatch_enqueues_due_subscription(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sub_id = await _seed_via(session_factory, DigestFrequency.DAILY, send_hour=8)

    with (
        patch.object(tasks, "SessionLocal", session_factory),
        patch.object(tasks.send_digest, "delay") as delay,
    ):
        # 09:00 UTC, past the 08:00 send-hour -> due.
        enqueued = await tasks._dispatch_digests_async(_utc(2026, 1, 15, 9))

    assert enqueued == 1
    delay.assert_called_once()
    assert delay.call_args.args[0] == str(sub_id)


async def test_dispatch_skips_when_not_yet_send_hour(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_via(session_factory, DigestFrequency.DAILY, send_hour=8)

    with (
        patch.object(tasks, "SessionLocal", session_factory),
        patch.object(tasks.send_digest, "delay") as delay,
    ):
        # 07:00 UTC, before the 08:00 send-hour -> not due.
        enqueued = await tasks._dispatch_digests_async(_utc(2026, 1, 15, 7))

    assert enqueued == 0
    delay.assert_not_called()


def test_dispatch_skips_when_lock_held() -> None:
    """The sync wrapper short-circuits (no DB / loop) when the leader lock is held."""
    with (
        patch.object(tasks, "RedisLock", _NeverLock),
        patch.object(tasks, "get_redis_client", lambda: object()),
        patch.object(tasks, "_dispatch_digests_async") as core,
    ):
        result = tasks.dispatch_digests()

    assert result == 0
    core.assert_not_called()


async def test_send_digest_marks_sent_and_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sub_id = await _seed_via(session_factory, DigestFrequency.DAILY, send_hour=8)
    now = _utc(2026, 1, 15, 9)
    recorder = services.RecordingEmailSender()

    with patch.object(tasks, "SessionLocal", session_factory):
        first = await tasks._send_digest_async(sub_id, now, sender=recorder)
        # A re-delivered task for the same period is a no-op.
        second = await tasks._send_digest_async(sub_id, now, sender=recorder)

    assert first is True
    assert second is False
    # Only the winning claim sent (the no-op second run did not).
    assert len(recorder.sent) == 1

    # last_sent_at / last_sent_period were recorded.
    async with session_factory() as session:
        sub = await services.get_digest_subscription_by_id(session, subscription_id=sub_id)
        assert sub is not None
        assert sub.last_sent_at is not None
        assert sub.last_sent_period == "daily:2026-01-15"


async def test_send_digest_renders_and_sends_via_mailer(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """send_digest renders the H4 email and hands it to the injected mailer."""
    sub_id = await _seed_via(session_factory, DigestFrequency.DAILY, send_hour=8)
    recorder = services.RecordingEmailSender()
    now = _utc(2026, 1, 15, 9)

    with patch.object(tasks, "SessionLocal", session_factory):
        sent = await tasks._send_digest_async(sub_id, now, sender=recorder)

    assert sent is True
    assert len(recorder.sent) == 1
    message = recorder.sent[0]
    # Addressed to the seeded recipient, branded subject, both bodies present.
    assert message.to.endswith("@example.com")
    assert "CivicSignals" in message.subject
    assert "Hot RFPs" in message.subject  # the saved-search name from _seed
    assert message.html_body is not None
    assert "Hot RFPs" in message.html_body
    assert "Hot RFPs" in message.text_body
