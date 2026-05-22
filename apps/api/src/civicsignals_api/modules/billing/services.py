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

All Stripe network calls are mediated through the ``StripeClient`` protocol so
tests can inject a mock without hitting the Stripe API.

# TODO N2: add ``assign_plan(workspace_id, plan)`` once plan definitions land.
# TODO N3: add ``record_usage(workspace_id, metric, delta)`` for metering.
# TODO N4: add ``check_limit(workspace_id, metric)`` for hard limits.
# TODO N5: add ``create_checkout_session`` / ``change_plan`` for self-serve flow.
# TODO LC-13: provision real STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

import stripe as stripe_sdk
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import get_settings
from civicsignals_api.ids import uuid7
from civicsignals_api.modules.billing.models import (
    BillingCustomer,
    BillingSubscription,
    BillingWebhookEvent,
    SubscriptionPlan,
    SubscriptionStatus,
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
        return stripe_sdk.Subscription.retrieve(
            subscription_id, api_key=self._api_key, **kwargs
        )


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
