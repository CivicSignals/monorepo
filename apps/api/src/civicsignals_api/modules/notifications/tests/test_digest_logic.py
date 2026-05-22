"""Pure due-selection logic tests for saved-search digests (H3).

No DB / Celery / live clock — exercises ``digest.is_due`` / ``digest.period_key``
with a fake ``now`` across timezone, daily/weekly, send-hour, and dedupe cases.
"""

from __future__ import annotations

from datetime import UTC, datetime

from civicsignals_api.modules.notifications.digest import (
    DigestFrequency,
    is_due,
    local_now,
    period_key,
    resolve_tz,
)


def _utc(year: int, month: int, day: int, hour: int) -> datetime:
    return datetime(year, month, day, hour, 0, 0, tzinfo=UTC)


# ---- resolve_tz / local_now ------------------------------------------------- #


def test_resolve_tz_falls_back_to_utc_on_bad_name() -> None:
    assert resolve_tz("Not/AReal_Zone").key == "UTC"
    assert resolve_tz("America/New_York").key == "America/New_York"


def test_local_now_projects_into_recipient_tz() -> None:
    # 12:00 UTC is 07:00 in New York (EST, winter; UTC-5).
    local = local_now(_utc(2026, 1, 15, 12), "America/New_York")
    assert local.hour == 7


def test_local_now_assumes_utc_for_naive_input() -> None:
    naive = datetime(2026, 1, 15, 12, 0, 0)
    local = local_now(naive, "UTC")
    assert local.hour == 12


# ---- period_key ------------------------------------------------------------- #


def test_period_key_off_is_none() -> None:
    assert period_key(DigestFrequency.OFF, _utc(2026, 1, 15, 12), "UTC") is None


def test_period_key_daily_is_local_calendar_day() -> None:
    # 02:00 UTC on the 16th is still the 15th in New York (21:00 EST).
    key = period_key(DigestFrequency.DAILY, _utc(2026, 1, 16, 2), "America/New_York")
    assert key == "daily:2026-01-15"


def test_period_key_weekly_is_local_iso_week() -> None:
    key = period_key(DigestFrequency.WEEKLY, _utc(2026, 1, 15, 12), "UTC")
    # 2026-01-15 is in ISO week 03.
    assert key == "weekly:2026-W03"


def test_period_key_stable_within_a_day() -> None:
    a = period_key(DigestFrequency.DAILY, _utc(2026, 1, 15, 8), "UTC")
    b = period_key(DigestFrequency.DAILY, _utc(2026, 1, 15, 20), "UTC")
    assert a == b


# ---- is_due: daily ---------------------------------------------------------- #


def test_daily_not_due_before_send_hour() -> None:
    assert not is_due(
        frequency=DigestFrequency.DAILY,
        send_hour=8,
        weekday=0,
        tz_name="UTC",
        now=_utc(2026, 1, 15, 7),
        last_sent_period=None,
    )


def test_daily_due_at_send_hour() -> None:
    assert is_due(
        frequency=DigestFrequency.DAILY,
        send_hour=8,
        weekday=0,
        tz_name="UTC",
        now=_utc(2026, 1, 15, 8),
        last_sent_period=None,
    )


def test_daily_due_after_send_hour_same_day() -> None:
    assert is_due(
        frequency=DigestFrequency.DAILY,
        send_hour=8,
        weekday=0,
        tz_name="UTC",
        now=_utc(2026, 1, 15, 14),
        last_sent_period=None,
    )


def test_daily_dedupe_suppresses_second_tick_same_day() -> None:
    # Already sent for 2026-01-15 -> a later tick the same day is not due.
    assert not is_due(
        frequency=DigestFrequency.DAILY,
        send_hour=8,
        weekday=0,
        tz_name="UTC",
        now=_utc(2026, 1, 15, 14),
        last_sent_period="daily:2026-01-15",
    )


def test_daily_due_again_next_day() -> None:
    assert is_due(
        frequency=DigestFrequency.DAILY,
        send_hour=8,
        weekday=0,
        tz_name="UTC",
        now=_utc(2026, 1, 16, 8),
        last_sent_period="daily:2026-01-15",
    )


def test_daily_send_hour_evaluated_in_recipient_tz() -> None:
    # 12:00 UTC = 07:00 New York: below an 08:00 NY send-hour, so not yet due...
    assert not is_due(
        frequency=DigestFrequency.DAILY,
        send_hour=8,
        weekday=0,
        tz_name="America/New_York",
        now=_utc(2026, 1, 15, 12),
        last_sent_period=None,
    )
    # ...but 13:00 UTC = 08:00 New York is due.
    assert is_due(
        frequency=DigestFrequency.DAILY,
        send_hour=8,
        weekday=0,
        tz_name="America/New_York",
        now=_utc(2026, 1, 15, 13),
        last_sent_period=None,
    )


# ---- is_due: weekly --------------------------------------------------------- #


def test_weekly_due_only_on_configured_weekday() -> None:
    # 2026-01-15 is a Thursday (isoweekday 4 -> our weekday 3). weekday=0 (Mon).
    assert not is_due(
        frequency=DigestFrequency.WEEKLY,
        send_hour=8,
        weekday=0,  # Monday
        tz_name="UTC",
        now=_utc(2026, 1, 15, 12),
        last_sent_period=None,
    )
    # weekday=3 (Thursday) matches.
    assert is_due(
        frequency=DigestFrequency.WEEKLY,
        send_hour=8,
        weekday=3,
        tz_name="UTC",
        now=_utc(2026, 1, 15, 12),
        last_sent_period=None,
    )


def test_weekly_dedupe_suppresses_second_tick_same_week() -> None:
    assert not is_due(
        frequency=DigestFrequency.WEEKLY,
        send_hour=8,
        weekday=3,
        tz_name="UTC",
        now=_utc(2026, 1, 15, 14),
        last_sent_period="weekly:2026-W03",
    )


def test_weekly_due_next_week() -> None:
    # The following Thursday (2026-01-22) is ISO week 04.
    assert is_due(
        frequency=DigestFrequency.WEEKLY,
        send_hour=8,
        weekday=3,
        tz_name="UTC",
        now=_utc(2026, 1, 22, 12),
        last_sent_period="weekly:2026-W03",
    )


# ---- is_due: off ------------------------------------------------------------ #


def test_off_is_never_due() -> None:
    assert not is_due(
        frequency=DigestFrequency.OFF,
        send_hour=0,
        weekday=0,
        tz_name="UTC",
        now=_utc(2026, 1, 15, 23),
        last_sent_period=None,
    )
