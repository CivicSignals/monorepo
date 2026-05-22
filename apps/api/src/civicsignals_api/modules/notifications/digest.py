"""Pure due-selection + period-key logic for saved-search digests (H3).

The scheduling rules live here, free of any DB / Celery / clock dependency, so
they can be unit-tested with a fake "now" and reused by the beat task. The two
public predicates are:

- :func:`period_key` — the dedupe key for a single (subscription, now) pair: the
  *local* calendar day (daily) or ISO week (weekly) in the recipient's timezone.
  Two ticks within the same local day/week produce the same key, so a digest is
  sent at most once per period even if the hourly beat fires twice (or a worker
  retries). This mirrors the M5 FOIA reminder "same-calendar-day guard".
- :func:`is_due` — whether a subscription should send *now*: the recipient's local
  hour has reached the configured ``send_hour`` (daily) and, for weekly, the local
  weekday matches ``weekday``, AND the current period has not already been sent
  (``last_sent_period``).

Timezone handling: every comparison is done in the recipient's own IANA timezone
(``zoneinfo``). The stored ``timezone`` defaults to UTC; a per-user tz preference
is a later concern (see the ``# TODO`` in ``models.py``). An unknown/invalid tz
name falls back to UTC rather than raising, so a bad row never wedges the whole
dispatch sweep.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class DigestFrequency(StrEnum):
    """How often a saved-search digest is delivered (H3).

    ``OFF`` means no digest is scheduled (the row may still exist so the user's
    choice is remembered when they toggle it back on). ``DAILY`` sends once per
    local calendar day at ``send_hour``; ``WEEKLY`` once per local ISO week on
    ``weekday`` at ``send_hour``.
    """

    OFF = "off"
    DAILY = "daily"
    WEEKLY = "weekly"


def resolve_tz(name: str) -> ZoneInfo:
    """Resolve an IANA tz name, falling back to UTC on an unknown/invalid value.

    A malformed tz on one subscription row must never raise mid-sweep and skip
    every later subscription — so we degrade to UTC instead of propagating.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def local_now(now: datetime, tz_name: str) -> datetime:
    """Project an aware ``now`` into the recipient's local timezone.

    ``now`` must be timezone-aware; a naive value is assumed UTC (defensive — the
    beat task always passes an aware ``datetime.now(timezone.utc)``).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(resolve_tz(tz_name))


def period_key(frequency: DigestFrequency, now: datetime, tz_name: str) -> str | None:
    """The dedupe key for the local period ``now`` falls in, or ``None`` if off.

    - daily  -> ``"daily:YYYY-MM-DD"`` (local calendar day)
    - weekly -> ``"weekly:GGGG-Www"`` (local ISO year + week)

    Storing the *period* the last send belonged to (rather than only a timestamp)
    makes the due check a cheap string compare and immune to clock skew within a
    period: a second tick in the same local day/week sees an equal key and is a
    no-op.
    """
    if frequency is DigestFrequency.OFF:
        return None
    local = local_now(now, tz_name)
    if frequency is DigestFrequency.DAILY:
        return f"daily:{local:%Y-%m-%d}"
    iso = local.isocalendar()
    return f"weekly:{iso.year:04d}-W{iso.week:02d}"


def is_due(
    *,
    frequency: DigestFrequency,
    send_hour: int,
    weekday: int,
    tz_name: str,
    now: datetime,
    last_sent_period: str | None,
) -> bool:
    """Whether this subscription should send a digest at ``now`` (recipient-local).

    Returns ``True`` iff:

    1. ``frequency`` is not ``off``;
    2. the recipient's *local* hour has reached ``send_hour`` (``>=`` so an hourly
       tick that lands a little after the boundary still fires within the day);
    3. for weekly, the recipient's local ISO weekday matches ``weekday``
       (``isoweekday`` is 1=Mon..7=Sun; we accept 0..6 = Mon..Sun and normalize);
    4. the current local period has not already been sent (``last_sent_period``).

    The combination of (2) the same-day hour gate and (4) the period dedupe means
    the first hourly tick at-or-after ``send_hour`` sends, and every later tick the
    same local day/week is suppressed.
    """
    if frequency is DigestFrequency.OFF:
        return False

    local = local_now(now, tz_name)

    if local.hour < send_hour:
        return False

    # Weekly only fires on the configured weekday. Accept 0..6 (Mon..Sun);
    # ``isoweekday`` is 1..7 (Mon..Sun).
    if frequency is DigestFrequency.WEEKLY and (local.isoweekday() - 1) != (weekday % 7):
        return False

    key = period_key(frequency, now, tz_name)
    return key is not None and key != last_sent_period
