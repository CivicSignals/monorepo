"""Public service interface for the billing module.

Other modules call billing only through the functions defined here — never by
importing billing's models or routes directly (doc 06 §3).

N1 implements:
  - :func:`get_or_create_stripe_client` — injectable Stripe client factory.
  - :func:`get_or_create_customer` — idempotently create/retrieve a Stripe customer.
  - :func:`get_subscription` — load the billing_subscription row for a workspace.
  - :func:`get_or_create_subscription` — upsert the local subscription row.
  - :func:`apply_subscription_updated` — handle ``customer.subscription.updated``.
  - :func:`apply_invoice_paid` — handle ``invoice.paid``.
  - :func:`apply_invoice_payment_failed`` — handle ``invoice.payment_failed``.
  - :func:`record_webhook_event` — write to the idempotency event-log.
  - :func:`is_event_already_processed` — dedupe check before side-effects.

N2 implements:
  - :func:`get_workspace_plan` — resolve a workspace's effective plan (cheap, no Stripe call).
  - :func:`plan_allows` — check whether a plan includes a feature flag.
  - :func:`plan_limit` — retrieve a numeric quota limit for a plan/dimension pair.

N3 implements:
  - :func:`current_period` — return the first day of the current UTC calendar month.
  - :func:`record_usage` — idempotent-friendly upsert/increment a usage counter.
  - :func:`get_usage` — read per-dimension totals for a workspace+period.
  - :func:`get_seats_count` — derive the current active seat count from accounts_member.
  - :func:`refresh_seats_usage` — update billing_usage seats snapshot for a workspace.
  - :func:`record_ai_run` — hook called by the LLM gateway accountant (N3 integration).

All Stripe network calls are mediated through the ``StripeClient`` protocol so
tests can inject a mock without hitting the Stripe API.

# TODO N4: add ``check_limit(workspace_id, metric)`` for hard limits (reads record_usage
#   output and plan_limit to enforce soft 80% banner + hard 429).
# TODO N5: add ``create_checkout_session`` / ``change_plan`` for self-serve flow.
# TODO LC-13: provision real STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, Protocol

import stripe as stripe_sdk
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import get_settings
from civicsignals_api.ids import uuid7
from civicsignals_api.modules.billing.models import (
    BillingCustomer,
    BillingSubscription,
    BillingUsage,
    BillingWebhookEvent,
    SubscriptionPlan,
    SubscriptionStatus,
)
from civicsignals_api.modules.billing.plans import (
    Dimension,
    Feature,
)
from civicsignals_api.modules.billing.plans import (
    plan_allows as _plan_allows_impl,
)
from civicsignals_api.modules.billing.plans import (
    plan_limit as _plan_limit_impl,
)

# ---------------------------------------------------------------------------
# Stripe client protocol (injectable for testing)
# ---------------------------------------------------------------------------


class StripeCustomersResource(Protocol):
    """Protocol for the Stripe customers resource (N1, injectable for tests)."""

    def create(self, **kwargs: Any) -> Any: ...

    def retrieve(self, customer_id: str, **kwargs: Any) -> Any: ...


class StripeSubscriptionsResource(Protocol):
    """Protocol for the Stripe subscriptions resource (N1, injectable for tests)."""

    def retrieve(self, subscription_id: str, **kwargs: Any) -> Any: ...


class StripeClient(Protocol):
    """Minimal Stripe client interface used by billing services (N1).

    Only the methods actually called by this module are part of the protocol;
    tests provide a mock that satisfies it. Top-level (non-nested) protocols
    satisfy mypy strict mode without ``type: ignore`` workarounds.
    """

    @property
    def customers(self) -> StripeCustomersResource: ...

    @property
    def subscriptions(self) -> StripeSubscriptionsResource: ...


class _StripeCustomers:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def create(self, **kwargs: Any) -> Any:
        return stripe_sdk.Customer.create(api_key=self._api_key, **kwargs)

    def retrieve(self, customer_id: str, **kwargs: Any) -> Any:
        return stripe_sdk.Customer.retrieve(customer_id, api_key=self._api_key, **kwargs)


class _StripeSubscriptions:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def retrieve(self, subscription_id: str, **kwargs: Any) -> Any:
        return stripe_sdk.Subscription.retrieve(subscription_id, api_key=self._api_key, **kwargs)


class _DefaultStripeClient:
    """Thin wrapper around the stripe SDK configured from Settings.

    Instantiated once per process (via :func:`get_stripe_client`). Tests
    replace it via dependency injection.
    """

    def __init__(self, api_key: str) -> None:
        self._customers_res = _StripeCustomers(api_key)
        self._subscriptions_res = _StripeSubscriptions(api_key)

    @property
    def customers(self) -> _StripeCustomers:
        return self._customers_res

    @property
    def subscriptions(self) -> _StripeSubscriptions:
        return self._subscriptions_res


_cached_stripe_client: StripeClient | None = None


def get_stripe_client() -> StripeClient:
    """Return the process-level Stripe client (singleton, lazily created).

    When ``STRIPE_SECRET_KEY`` is not configured (dev / self-host), raises
    :class:`BillingNotConfiguredError` so callers can short-circuit gracefully.

    Tests override this function via :func:`override_stripe_client`.
    """
    global _cached_stripe_client  # pylint: disable=global-statement
    if _cached_stripe_client is not None:
        return _cached_stripe_client
    settings = get_settings()
    if not settings.stripe_secret_key:
        raise BillingNotConfiguredError("STRIPE_SECRET_KEY is not set")
    _cached_stripe_client = _DefaultStripeClient(settings.stripe_secret_key)
    return _cached_stripe_client


def override_stripe_client(client: StripeClient) -> None:
    """Inject a mock Stripe client; call in test setup."""
    global _cached_stripe_client  # pylint: disable=global-statement
    _cached_stripe_client = client


def reset_stripe_client() -> None:
    """Clear the cached client; call in test teardown."""
    global _cached_stripe_client  # pylint: disable=global-statement
    _cached_stripe_client = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class BillingNotConfiguredError(RuntimeError):
    """Raised when billing is accessed but STRIPE_SECRET_KEY is not set."""


class DuplicateWebhookEvent(Exception):
    """Raised when a Stripe event id has already been processed (idempotency)."""

    def __init__(self, event_id: str) -> None:
        super().__init__(f"Stripe event {event_id!r} already processed")
        self.event_id = event_id


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------


async def get_customer_by_workspace(
    session: AsyncSession, workspace_id: uuid.UUID
) -> BillingCustomer | None:
    """Return the BillingCustomer row for a workspace, or None."""
    result = await session.execute(
        select(BillingCustomer).where(BillingCustomer.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_customer(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    email: str | None = None,
    name: str | None = None,
    *,
    stripe_client: StripeClient | None = None,
) -> BillingCustomer:
    """Idempotently create (or retrieve) a Stripe customer for a workspace.

    If a ``BillingCustomer`` row already exists for ``workspace_id``, the
    existing row is returned unchanged.  Otherwise, a new Stripe customer is
    created via the API and the row is persisted.

    ``email`` / ``name`` are passed to Stripe for the customer object (used in
    the Stripe dashboard).  ``stripe_client`` allows injection in tests; the
    default is the process-level singleton.
    """
    existing = await get_customer_by_workspace(session, workspace_id)
    if existing is not None:
        return existing

    client = stripe_client if stripe_client is not None else get_stripe_client()
    metadata: dict[str, str] = {"workspace_id": str(workspace_id)}
    create_kwargs: dict[str, Any] = {"metadata": metadata}
    if email:
        create_kwargs["email"] = email
    if name:
        create_kwargs["name"] = name

    stripe_customer = client.customers.create(**create_kwargs)
    customer = BillingCustomer(
        id=uuid7(),
        workspace_id=workspace_id,
        stripe_customer_id=stripe_customer["id"],
    )
    session.add(customer)
    await session.flush()
    return customer


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


async def get_subscription(
    session: AsyncSession, workspace_id: uuid.UUID
) -> BillingSubscription | None:
    """Return the BillingSubscription row for a workspace, or None."""
    result = await session.execute(
        select(BillingSubscription).where(BillingSubscription.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def get_or_create_subscription(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    plan: SubscriptionPlan = SubscriptionPlan.SOLO,
    status: SubscriptionStatus = SubscriptionStatus.TRIALING,
    stripe_subscription_id: str | None = None,
    stripe_price_id: str | None = None,
    current_period_end: datetime | None = None,
    trial_ends_at: datetime | None = None,
) -> BillingSubscription:
    """Get or create the subscription row for a workspace.

    Idempotent: if a row already exists it is returned unchanged (caller is
    responsible for updates via the webhook handlers).
    """
    existing = await get_subscription(session, workspace_id)
    if existing is not None:
        return existing

    sub = BillingSubscription(
        id=uuid7(),
        workspace_id=workspace_id,
        plan=plan,
        status=status,
        stripe_subscription_id=stripe_subscription_id,
        stripe_price_id=stripe_price_id,
        current_period_end=current_period_end,
        trial_ends_at=trial_ends_at,
    )
    session.add(sub)
    await session.flush()
    return sub


# ---------------------------------------------------------------------------
# Webhook event log
# ---------------------------------------------------------------------------


async def is_event_already_processed(session: AsyncSession, stripe_event_id: str) -> bool:
    """Return True if we have already processed this Stripe event id."""
    result = await session.execute(
        select(BillingWebhookEvent).where(BillingWebhookEvent.stripe_event_id == stripe_event_id)
    )
    return result.scalar_one_or_none() is not None


async def record_webhook_event(
    session: AsyncSession,
    stripe_event_id: str,
    event_type: str,
    *,
    status: str = "handled",
    note: str | None = None,
) -> BillingWebhookEvent:
    """Insert a billing_webhook_event row.

    Must be called *before* any state mutations so that, on replay, the event
    is already logged even if downstream mutation fails.  Callers wrap the
    whole webhook handler in a transaction so a failure rolls back both.
    """
    event = BillingWebhookEvent(
        id=uuid7(),
        stripe_event_id=stripe_event_id,
        event_type=event_type,
        status=status,
        note=note,
    )
    session.add(event)
    await session.flush()
    return event


# ---------------------------------------------------------------------------
# Subscription state machine (driven by Stripe events)
# ---------------------------------------------------------------------------

# Map Stripe subscription status → our SubscriptionStatus.
_STRIPE_STATUS_MAP: dict[str, SubscriptionStatus] = {
    "trialing": SubscriptionStatus.TRIALING,
    "active": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE,
    "canceled": SubscriptionStatus.CANCELLED,
    "unpaid": SubscriptionStatus.PAST_DUE,  # treat unpaid as past_due
    "incomplete": SubscriptionStatus.PAST_DUE,
    "incomplete_expired": SubscriptionStatus.CANCELLED,
    "paused": SubscriptionStatus.READ_ONLY,
}


def _parse_ts(unix: int | None) -> datetime | None:
    """Convert a Stripe Unix timestamp to an aware UTC datetime, or None."""
    if unix is None:
        return None
    return datetime.fromtimestamp(unix, tz=UTC)


async def apply_subscription_updated(
    session: AsyncSession,
    stripe_sub_obj: dict[str, Any],
) -> BillingSubscription | None:
    """Handle ``customer.subscription.updated``.

    Updates ``status``, ``stripe_price_id``, and ``current_period_end`` on
    the matching ``billing_subscription`` row (looked up by
    ``stripe_subscription_id``).  Returns the updated row, or None if no row
    is found (e.g. if the subscription was created externally before N1 was
    deployed — safe to ignore).
    """
    stripe_sub_id: str = stripe_sub_obj["id"]
    result = await session.execute(
        select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == stripe_sub_id
        )
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return None

    raw_status: str = stripe_sub_obj.get("status", "")
    new_status = _STRIPE_STATUS_MAP.get(raw_status, SubscriptionStatus.PAST_DUE)

    # Extract the first item's price id (simple single-price subscriptions).
    items_data = stripe_sub_obj.get("items", {}).get("data", [])
    price_id: str | None = None
    if items_data:
        price_id = items_data[0].get("price", {}).get("id")

    sub.status = new_status
    sub.stripe_price_id = price_id
    sub.current_period_end = _parse_ts(stripe_sub_obj.get("current_period_end"))
    sub.payment_failed = False  # cleared on successful update event
    await session.flush()
    return sub


async def apply_invoice_paid(
    session: AsyncSession,
    stripe_invoice_obj: dict[str, Any],
) -> BillingSubscription | None:
    """Handle ``invoice.paid``.

    Marks the subscription as ``active`` and clears ``payment_failed``.
    Returns the updated row, or None if no subscription was found.
    """
    stripe_sub_id: str | None = stripe_invoice_obj.get("subscription")
    if not stripe_sub_id:
        return None
    result = await session.execute(
        select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == stripe_sub_id
        )
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return None

    sub.status = SubscriptionStatus.ACTIVE
    sub.payment_failed = False
    sub.current_period_end = _parse_ts(stripe_invoice_obj.get("period_end"))
    await session.flush()
    return sub


async def apply_invoice_payment_failed(
    session: AsyncSession,
    stripe_invoice_obj: dict[str, Any],
) -> BillingSubscription | None:
    """Handle ``invoice.payment_failed``.

    Sets ``payment_failed = True`` and moves status to ``past_due`` (doc 07
    §4: "3 failed Stripe charges → workspace put in read_only mode").  The
    escalation to ``read_only`` / ``suspended`` is driven by a subsequent
    ``customer.subscription.updated`` event from Stripe (when Stripe changes the
    subscription status after its retry schedule concludes), not by this handler
    directly.

    # TODO N4: when status becomes past_due/read_only, enforce export/push limits
    #   via billing.services.check_limit.
    """
    stripe_sub_id: str | None = stripe_invoice_obj.get("subscription")
    if not stripe_sub_id:
        return None
    result = await session.execute(
        select(BillingSubscription).where(
            BillingSubscription.stripe_subscription_id == stripe_sub_id
        )
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        return None

    sub.payment_failed = True
    # Only advance to past_due if currently active/trialing (don't downgrade
    # an already-suspended subscription).
    if sub.status in (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING):
        sub.status = SubscriptionStatus.PAST_DUE
    await session.flush()
    return sub


# ---------------------------------------------------------------------------
# N2: Plan resolution + feature-gate helpers
# ---------------------------------------------------------------------------

# Plans that represent an active subscription (N2 § effective-plan resolution).
# A workspace in READ_ONLY or PAST_DUE still holds its plan (the rep can still
# read data); CANCELLED and SUSPENDED fall through to SELF_HOSTED.
_ACTIVE_LIKE_STATUSES: frozenset[SubscriptionStatus] = frozenset(
    {
        SubscriptionStatus.TRIALING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.PAST_DUE,
        SubscriptionStatus.READ_ONLY,
    }
)


async def get_workspace_plan(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> SubscriptionPlan:
    """Return the effective :class:`SubscriptionPlan` for a workspace (N2).

    Resolution logic (no Stripe call — reads only from ``billing_subscription``):

    1. If no subscription row exists → ``SELF_HOSTED`` (free default).
    2. If the subscription status is ``CANCELLED`` or ``SUSPENDED`` → ``SELF_HOSTED``.
    3. Otherwise → the plan stored on the subscription row.

    This is cheap (one DB read) and safe to call on every request. The
    ``billing_subscription`` row is kept current by N1's webhook handlers.
    """
    sub = await get_subscription(session, workspace_id)
    if sub is None:
        return SubscriptionPlan.SELF_HOSTED
    if sub.status not in _ACTIVE_LIKE_STATUSES:
        return SubscriptionPlan.SELF_HOSTED
    return sub.plan


def plan_allows(plan: SubscriptionPlan, feature: Feature) -> bool:
    """Return ``True`` if ``plan`` includes ``feature`` (N2).

    Thin wrapper around :func:`plans.plan_allows` so other modules can import
    from ``billing.services`` without knowing about ``plans.py``.
    """
    return _plan_allows_impl(plan, feature)


def plan_limit(plan: SubscriptionPlan, dimension: Dimension) -> int | None:
    """Return the numeric cap for ``dimension`` on ``plan``, or ``None`` if unlimited (N2).

    Thin wrapper around :func:`plans.plan_limit` so other modules import from
    ``billing.services`` only.
    """
    return _plan_limit_impl(plan, dimension)


# ---------------------------------------------------------------------------
# N3: Usage metering
# ---------------------------------------------------------------------------


def current_period() -> date:
    """Return the first day of the current UTC calendar month (N3).

    Each billing period is a calendar month.  Usage rows are keyed on
    ``(workspace_id, period, dimension)`` where ``period = current_period()``.
    A new month automatically creates new rows; old rows are preserved for
    audit/analytics.
    """
    today = datetime.now(tz=UTC).date()
    return today.replace(day=1)


async def record_usage(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    dimension: Dimension,
    delta: int = 1,
    *,
    period: date | None = None,
) -> None:
    """Increment a usage counter for ``(workspace_id, period, dimension)`` (N3).

    Uses a PostgreSQL ``INSERT … ON CONFLICT DO UPDATE`` (upsert) to increment
    atomically in a single round-trip — no read-before-write.  Safe to call
    from concurrent workers on the same row.

    ``period`` defaults to the current billing month.  Pass an explicit period
    in tests to control the billing window.

    For ``seats``, prefer :func:`refresh_seats_usage` (which overwrites rather
    than accumulating) because the seat count is a snapshot, not a rate.

    .. note::
        This function does **not** begin a transaction.  The caller is
        responsible for wrapping the session in a transaction (or letting
        FastAPI's dependency-injected session auto-commit).
    """
    p = period or current_period()
    stmt = (
        pg_insert(BillingUsage)
        .values(
            id=uuid7(),
            workspace_id=workspace_id,
            period=p,
            dimension=dimension.value,
            count=delta,
        )
        .on_conflict_do_update(
            index_elements=["workspace_id", "period", "dimension"],
            set_={"count": BillingUsage.count + delta, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)


async def set_usage(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    dimension: Dimension,
    value: int,
    *,
    period: date | None = None,
) -> None:
    """Overwrite (set) a usage counter for ``(workspace_id, period, dimension)`` (N3).

    Used for snapshot-style dimensions like ``seats`` where the value should be
    the current total, not an accumulated delta.  Uses the same upsert pattern
    as :func:`record_usage` but sets the count directly rather than adding.
    """
    p = period or current_period()
    stmt = (
        pg_insert(BillingUsage)
        .values(
            id=uuid7(),
            workspace_id=workspace_id,
            period=p,
            dimension=dimension.value,
            count=value,
        )
        .on_conflict_do_update(
            index_elements=["workspace_id", "period", "dimension"],
            set_={"count": value, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)


async def get_usage(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    period: date | None = None,
) -> dict[str, int]:
    """Return per-dimension usage totals for ``workspace_id`` in ``period`` (N3).

    Returns a mapping of :class:`~civicsignals_api.modules.billing.plans.Dimension`
    string value → count.  Dimensions with no recorded usage are not in the dict
    (callers should treat missing keys as 0).

    The ``period`` defaults to the current billing month.
    """
    p = period or current_period()
    result = await session.execute(
        select(BillingUsage.dimension, BillingUsage.count).where(
            BillingUsage.workspace_id == workspace_id,
            BillingUsage.period == p,
        )
    )
    return {str(row[0]): int(row[1]) for row in result}


async def get_seats_count(session: AsyncSession, workspace_id: uuid.UUID) -> int:
    """Return the current number of active members in a workspace (N3).

    Counts rows in ``accounts_member`` for this workspace.  Used by the
    seats-usage refresh path (:func:`refresh_seats_usage`) and by the usage
    endpoint to surface live seat counts.

    No module imports accounts internals directly (doc 06 §3): we issue a
    raw count query against the ``accounts_member`` table name here rather
    than importing the Membership model — the column is stable and well-known.
    """
    from sqlalchemy import text

    row = await session.execute(
        text(
            "SELECT COUNT(*) FROM accounts_member WHERE workspace_id = :ws_id"
        ),
        {"ws_id": str(workspace_id)},
    )
    return int(row.scalar_one())


async def refresh_seats_usage(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    period: date | None = None,
) -> int:
    """Snapshot the current seat count into billing_usage (N3).

    Unlike other dimensions (which are incremented), seats is a snapshot of the
    current member count.  This function reads the count from ``accounts_member``
    and writes it to ``billing_usage`` using :func:`set_usage`.

    Called by the billing scheduler task and by the usage endpoint on-demand so
    the seat count is always fresh.

    Returns the snapshotted seat count.
    """
    count = await get_seats_count(session, workspace_id)
    await set_usage(session, workspace_id, Dimension.SEATS, count, period=period)
    return count


async def record_ai_run(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    delta: int = 1,
    *,
    period: date | None = None,
) -> None:
    """Record one or more LLM gateway calls for a workspace (N3).

    This is the integration seam the LLM gateway (E2) calls after each
    ``complete()`` invocation where a ``workspace_id`` is known.  It increments
    the ``ai_runs_per_month`` dimension in billing_usage.

    The gateway's in-process :class:`~civicsignals_api.llm_gateway.accounting.InMemoryTokenAccountant`
    already accumulates per-workspace call counts; this function persists them
    to Postgres so they survive process restarts and span multiple workers.

    Typical call site (in the gateway or a task wrapper)::

        await billing_services.record_ai_run(session, workspace_id=uuid.UUID(workspace_id))

    # TODO N4: when ``ai_runs_per_month`` usage reaches the plan limit, raise a
    #   429 via ``check_limit`` before dispatching the next LLM call.
    """
    await record_usage(session, workspace_id, Dimension.AI_RUNS_PER_MONTH, delta, period=period)


# ---------------------------------------------------------------------------
# N3: API-request metering hook
# ---------------------------------------------------------------------------

# NOTE: Full per-request metering middleware is intentionally deferred.
# Incrementing billing_usage on every authenticated HTTP request would add a
# DB write to every hot-path request — a non-trivial cost at 8M+ req/day.
#
# The recommended approach (when N4 enforcement is needed) is a periodic flush:
# 1. Increment an in-Redis counter on each request (O(1), sub-ms).
# 2. A background task (Celery beat, e.g. every 60s) reads the Redis counter,
#    calls record_usage(Dimension.API_REQUESTS_PER_MONTH, delta=counter_value),
#    and resets the Redis key.
#
# This function is the seam that background task should call.  For now it is
# a thin wrapper around record_usage; the caller supplies the delta (batch size).
#
# TODO N4: wire up the Redis counter + Celery beat flush task.
async def record_api_requests(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    delta: int = 1,
    *,
    period: date | None = None,
) -> None:
    """Record ``delta`` authenticated API requests for a workspace (N3).

    Intended to be called from a periodic flush task rather than on every
    request (see module-level note above).  Direct per-request call is fine
    for low-volume workspaces or tests.

    # TODO N4: wire the Redis counter + beat task; the per-request call site
    #   in middleware should read:
    #       redis_client.incr(f"api_req:{workspace_id}:{current_period()}")
    #   and a beat task drains it here every 60 s.
    """
    await record_usage(
        session, workspace_id, Dimension.API_REQUESTS_PER_MONTH, delta, period=period
    )
