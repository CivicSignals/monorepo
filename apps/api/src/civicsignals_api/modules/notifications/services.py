"""Public service interface for the notifications module.

Other modules call notifications only through the functions defined here — never
by importing notifications's models or routes directly (doc 06 §3).

For B1 this exposes a minimal, injectable transactional-email sender. In dev the
default transport targets Mailpit via the SMTP settings already in
``config.py``. The transport is a small :class:`EmailSender` protocol so tests
(and B-series work) can substitute an in-memory recorder without touching SMTP.
Digest/notification models and the full template system are later epics; this is
the transactional-mail seam (verification, password reset, invites).
"""

from __future__ import annotations

import smtplib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Protocol, cast

import structlog
from sqlalchemy import CursorResult, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import get_settings

from .digest import DigestFrequency, is_due, period_key
from .models import DigestSubscription

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class OutboundEmail:
    """A rendered transactional email ready to send.

    ``headers`` carries extra RFC 5322 headers (H5 uses it for ``List-Unsubscribe``
    / ``List-Unsubscribe-Post`` so mail clients show a native one-click unsubscribe
    button — RFC 8058). Empty by default; the SMTP transport sets each entry.
    """

    to: str
    subject: str
    text_body: str
    html_body: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class EmailSender(Protocol):
    """Pluggable transport for transactional email (mockable in tests)."""

    def send(self, message: OutboundEmail) -> None: ...


class SMTPEmailSender:
    """Sends mail over SMTP. Defaults target the dev Mailpit instance.

    No TLS/auth in dev (Mailpit needs none); production self-host configures the
    operator's relay via the same ``SMTP_*`` env vars. Email/webhook delivery is
    a `worker_notify` concern at scale, but transactional verification mail is
    sent inline from the request path in MVP.
    """

    def __init__(self, host: str, port: int, from_addr: str) -> None:
        self._host = host
        self._port = port
        self._from_addr = from_addr

    def send(self, message: OutboundEmail) -> None:
        msg = EmailMessage()
        msg["From"] = self._from_addr
        msg["To"] = message.to
        msg["Subject"] = message.subject
        # Extra headers (H5: List-Unsubscribe / List-Unsubscribe-Post) before the
        # body so the mail client sees them at the top of the message.
        for name, value in message.headers.items():
            msg[name] = value
        msg.set_content(message.text_body)
        if message.html_body is not None:
            msg.add_alternative(message.html_body, subtype="html")
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            smtp.send_message(msg)


@dataclass
class RecordingEmailSender:
    """In-memory sender for tests — records outbound mail instead of sending."""

    sent: list[OutboundEmail] = field(default_factory=list)

    def send(self, message: OutboundEmail) -> None:
        self.sent.append(message)


def default_email_sender() -> EmailSender:
    """Build the SMTP sender from settings (dev = Mailpit)."""
    settings = get_settings()
    return SMTPEmailSender(settings.smtp_host, settings.smtp_port, settings.email_from)


def send_email(message: OutboundEmail, *, sender: EmailSender | None = None) -> None:
    """Send a transactional email through ``sender`` (defaults to SMTP).

    Failures are logged and swallowed so a flaky mail relay never breaks the
    surrounding request (e.g. signup). Callers that must guarantee delivery
    should enqueue via ``worker_notify`` instead (later epics).
    """
    transport = sender or default_email_sender()
    try:
        transport.send(message)
    except Exception:
        # Best-effort transactional send: never break the surrounding request
        # (e.g. signup) on a flaky mail relay.
        logger.warning("transactional_email_send_failed", to=message.to, subject=message.subject)


# --------------------------------------------------------------------------- #
# H3: saved-search digest subscriptions                                       #
# --------------------------------------------------------------------------- #
#
# The notifications module owns delivery, so it owns the per-(saved-search, user)
# digest schedule (``DigestSubscription``). Other modules call these functions
# rather than touching the model directly (doc 06 §3). The dispatch beat task
# (``tasks.py``) reads through :func:`list_due_subscriptions` / :func:`mark_sent`;
# the routes write through :func:`upsert_digest_subscription` /
# :func:`get_digest_subscription`.


async def get_digest_subscription_by_id(
    session: AsyncSession,
    *,
    subscription_id: uuid.UUID,
) -> DigestSubscription | None:
    """A digest subscription by its primary key, or ``None`` (delivery path)."""
    return await session.get(DigestSubscription, subscription_id)


async def get_digest_subscription(
    session: AsyncSession,
    *,
    saved_search_id: uuid.UUID,
    user_id: uuid.UUID,
) -> DigestSubscription | None:
    """The caller's digest subscription for a saved search, or ``None``."""
    result = await session.execute(
        select(DigestSubscription).where(
            DigestSubscription.saved_search_id == saved_search_id,
            DigestSubscription.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def upsert_digest_subscription(
    session: AsyncSession,
    *,
    saved_search_id: uuid.UUID,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    frequency: DigestFrequency,
    send_hour: int = 8,
    weekday: int = 0,
    timezone: str = "UTC",
) -> DigestSubscription:
    """Create or update the caller's digest schedule for one saved search (H3).

    Idempotent on the unique ``(saved_search_id, user_id)`` pair via an
    ``ON CONFLICT … DO UPDATE`` upsert, so re-setting the frequency from the UI
    never collides. Changing the schedule clears ``last_sent_period`` so the next
    due period sends under the new cadence rather than being suppressed by a stale
    dedupe key. The caller commits.
    """
    stmt = (
        pg_insert(DigestSubscription)
        .values(
            id=uuid.uuid4(),
            saved_search_id=saved_search_id,
            workspace_id=workspace_id,
            user_id=user_id,
            frequency=frequency.value,
            send_hour=send_hour,
            weekday=weekday,
            timezone=timezone,
        )
        .on_conflict_do_update(
            constraint="uq_notifications_digest_subscription_search_user",
            set_={
                "frequency": frequency.value,
                "send_hour": send_hour,
                "weekday": weekday,
                "timezone": timezone,
                # A schedule change resets the dedupe so the new cadence's next
                # period is honoured (not suppressed by the prior period key).
                "last_sent_period": None,
            },
        )
        .returning(DigestSubscription.id)
    )
    result = await session.execute(stmt)
    sub_id = result.scalar_one()
    await session.flush()
    sub = await session.get(DigestSubscription, sub_id)
    assert sub is not None  # just upserted
    await session.refresh(sub)
    return sub


async def list_active_subscriptions(
    session: AsyncSession,
) -> list[DigestSubscription]:
    """All subscriptions with a non-``off`` frequency (the dispatch sweep input).

    The dispatch beat task pulls the (small) set of scheduled subscriptions and
    applies the pure :func:`~notifications.digest.is_due` predicate per row, rather
    than encoding the timezone math in SQL. The active partial index keeps this
    scan cheap.
    """
    result = await session.execute(
        select(DigestSubscription).where(DigestSubscription.frequency != DigestFrequency.OFF.value)
    )
    return list(result.scalars().all())


def select_due_subscriptions(
    subscriptions: list[DigestSubscription],
    *,
    now: datetime,
) -> list[DigestSubscription]:
    """Filter ``subscriptions`` to the ones due to send at ``now`` (pure).

    Pure (no DB / clock) so the selection is unit-testable with a fake ``now``;
    the timezone + daily/weekly + dedupe rules live in ``digest.is_due``.
    """
    return [
        sub
        for sub in subscriptions
        if is_due(
            frequency=sub.frequency_enum,
            send_hour=sub.send_hour,
            weekday=sub.weekday,
            tz_name=sub.timezone,
            now=now,
            last_sent_period=sub.last_sent_period,
        )
    ]


async def mark_sent(
    session: AsyncSession,
    *,
    subscription_id: uuid.UUID,
    now: datetime,
) -> bool:
    """Claim the current period for a subscription (distributed-safe dedupe).

    Atomically sets ``last_sent_period`` to the current local period **only if it
    differs** from the stored one (a guarded ``UPDATE … WHERE last_sent_period IS
    DISTINCT FROM :key``). Returns ``True`` iff this call won the claim — so two
    workers racing on the same due subscription, one wins and sends, the other
    sees ``False`` and skips (mirrors the M5 FOIA ``mark_reminded`` guard and the
    F6 lock intent). The caller commits.
    """
    sub = await session.get(DigestSubscription, subscription_id)
    if sub is None:
        return False
    key = period_key(sub.frequency_enum, now, sub.timezone)
    if key is None or key == sub.last_sent_period:
        return False
    # Guard on the period we read so a concurrent claim cannot double-send.
    stmt = (
        update(DigestSubscription)
        .where(
            DigestSubscription.id == subscription_id,
            DigestSubscription.last_sent_period.is_distinct_from(key),
        )
        .values(last_sent_period=key, last_sent_at=now)
    )
    result = cast(CursorResult[Any], await session.execute(stmt))
    return result.rowcount > 0


# --------------------------------------------------------------------------- #
# H5: unsubscribe + per-user preferences                                       #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UserSubscription:
    """A digest subscription joined with its saved-search name (prefs list, H5)."""

    subscription: DigestSubscription
    saved_search_name: str


async def list_user_subscriptions(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> list[UserSubscription]:
    """The caller's digest subscriptions in one workspace, with search names (H5).

    Powers the consolidated ``/settings/notifications`` preferences page: one row
    per saved search the caller has a subscription for (any frequency, including
    ``off`` so a previously-disabled digest can be re-enabled). Joined to
    ``searches_saved_search`` for the display name; workspace-scoped so a member
    only ever sees their own subscriptions in the active workspace.
    """
    from civicsignals_api.modules.searches.models import SavedSearch

    stmt = (
        select(DigestSubscription, SavedSearch.name)
        .join(SavedSearch, SavedSearch.id == DigestSubscription.saved_search_id)
        .where(
            DigestSubscription.user_id == user_id,
            DigestSubscription.workspace_id == workspace_id,
        )
        .order_by(SavedSearch.name)
    )
    rows = await session.execute(stmt)
    return [UserSubscription(subscription=sub, saved_search_name=name) for sub, name in rows.all()]


@dataclass(frozen=True)
class UnsubscribeOutcome:
    """Result of a token-driven unsubscribe (H5): whether a row matched + its name."""

    unsubscribed: bool
    saved_search_name: str | None


async def unsubscribe_by_id(
    session: AsyncSession,
    *,
    subscription_id: uuid.UUID,
) -> UnsubscribeOutcome:
    """Flip one subscription's frequency to ``off`` (the one-click unsubscribe, H5).

    Idempotent: setting an already-``off`` subscription to ``off`` is a no-op that
    still reports ``unsubscribed=True`` (the recipient's intent is satisfied either
    way). Keeps the row so re-subscribing later remembers nothing was deleted.
    Reports ``unsubscribed=False`` only when no such subscription exists. Also
    returns the saved-search name (best effort) so the confirm page can name the
    digest. The caller commits.

    Unlike the B3 reset, the unsubscribe *token* is stateless (no DB consume step):
    the action is idempotent, so there is nothing to mark used.
    """
    from civicsignals_api.modules.searches.models import SavedSearch

    sub = await session.get(DigestSubscription, subscription_id)
    if sub is None:
        return UnsubscribeOutcome(unsubscribed=False, saved_search_name=None)

    name = await session.scalar(
        select(SavedSearch.name).where(SavedSearch.id == sub.saved_search_id)
    )
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(DigestSubscription)
            .where(DigestSubscription.id == subscription_id)
            .values(frequency=DigestFrequency.OFF.value)
        ),
    )
    return UnsubscribeOutcome(unsubscribed=result.rowcount > 0, saved_search_name=name)


async def build_digest_payload(
    session: AsyncSession,
    *,
    subscription: DigestSubscription,
    since: datetime | None,
) -> dict[str, Any]:
    """Produce the digest payload for a due subscription (H3).

    Re-runs the saved search's stored filters against the workspace feed
    (``signals.list_workspace_signals``) bounded to signals published since the
    last send, and returns a serializable payload (recipient + saved search +
    matched signals). The H4 renderer (``templates.render_digest_email``) turns
    this into the email body that ``send_digest`` delivers.

    Cross-module reads go through the sanctioned service surfaces only (doc 06 §3):
    ``searches.services`` for the filter blob, ``signals.services`` for the feed,
    ``accounts.services`` for the recipient's email (the H4 renderer's ``to``).
    """
    from civicsignals_api.modules.accounts import services as accounts_services
    from civicsignals_api.modules.searches import services as searches_services
    from civicsignals_api.modules.signals import services as signals_services

    # Recipient address for the rendered digest (H4). May be ``None`` if the user
    # row vanished; the delivery task treats a missing address as "nothing to send".
    recipient = await accounts_services.get_user_by_id(session, subscription.user_id)
    recipient_email = recipient.email if recipient is not None else None

    search = await searches_services.get_saved_search(
        session,
        workspace_id=subscription.workspace_id,
        user_id=subscription.user_id,
        search_id=subscription.saved_search_id,
    )
    if search is None:
        # The saved search vanished between sweep and delivery (CASCADE should have
        # removed the subscription, but be defensive): empty payload, no signals.
        return {
            "saved_search_id": str(subscription.saved_search_id),
            "saved_search_name": None,
            "workspace_id": str(subscription.workspace_id),
            "user_id": str(subscription.user_id),
            "recipient_email": recipient_email,
            "signals": [],
        }

    filters: dict[str, Any] = dict(search.filters or {})
    # Bound to "new since last send": prefer the explicit ``since``; a stored
    # ``published_at_gte`` in the saved filters still applies (list_workspace_signals
    # takes the tighter of the two only if we pass the digest window — pass ``since``
    # when present, else fall back to the saved filter's own bound).
    published_at_gte = since if since is not None else _parse_dt(filters.get("published_at_gte"))

    page = await signals_services.list_workspace_signals(
        session,
        workspace_id=subscription.workspace_id,
        signal_type=filters.get("signal_type"),
        statuses=filters.get("statuses"),
        min_score=filters.get("min_score"),
        published_at_gte=published_at_gte,
        published_at_lt=_parse_dt(filters.get("published_at_lt")),
        limit=signals_services.DEFAULT_LIMIT,
    )

    signals = [
        {
            "signal_id": str(item.signal.id),
            "title": item.signal.title,
            "signal_type": item.signal.signal_type,
            "entity": item.signal.entity_name_raw,
            "occurred_at": (
                item.signal.occurred_at.isoformat() if item.signal.occurred_at else None
            ),
            "score": item.score,
            "status": item.status,
        }
        for item in page.items
    ]

    # The H4 renderer (``templates.render_digest_email``) turns this payload into the
    # branded HTML+text email; ``send_digest`` (tasks.py) renders + sends it.
    return {
        "saved_search_id": str(search.id),
        "saved_search_name": search.name,
        "workspace_id": str(subscription.workspace_id),
        "user_id": str(subscription.user_id),
        "recipient_email": recipient_email,
        "since": since.isoformat() if since is not None else None,
        "signals": signals,
    }


def _parse_dt(value: Any) -> datetime | None:
    """Coerce a stored ISO timestamp (or ``None``) to a ``datetime``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None
