"""Tests for N1: Stripe customer + subscription wiring.

Coverage:
  1. Customer ↔ workspace link (get_or_create_customer, idempotency).
  2. Subscription CRUD (get_or_create_subscription, get_subscription).
  3. Webhook signature verification (valid / invalid).
  4. Idempotent event processing (same event id twice → one effect).
  5. customer.subscription.updated → BillingSubscription state.
  6. invoice.paid → BillingSubscription active + payment_failed cleared.
  7. invoice.payment_failed → BillingSubscription past_due + payment_failed.
  8. Unknown event type → ignored (200, no crash).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.modules.billing import services as billing_services
from civicsignals_api.modules.billing.models import SubscriptionPlan, SubscriptionStatus
from civicsignals_api.modules.billing.tests.conftest import (
    make_invoice_paid_event,
    make_invoice_payment_failed_event,
    make_mock_stripe_client,
    make_subscription_updated_event,
    signed_webhook_request,
)

# ---------------------------------------------------------------------------
# 1. Customer ↔ workspace link
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_customer_creates_row(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    mock_client = make_mock_stripe_client("cus_abc_123")
    billing_services.override_stripe_client(mock_client)

    customer = await billing_services.get_or_create_customer(
        session,
        workspace_id,
        email="test@example.com",
        name="Test Workspace",
        stripe_client=mock_client,
    )

    assert customer.workspace_id == workspace_id
    assert customer.stripe_customer_id == "cus_abc_123"
    mock_client.customers.create.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_customer_is_idempotent(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    mock_client = make_mock_stripe_client("cus_idempotent")

    first = await billing_services.get_or_create_customer(
        session, workspace_id, stripe_client=mock_client
    )
    # Flush first call so the row exists.
    await session.flush()

    second = await billing_services.get_or_create_customer(
        session, workspace_id, stripe_client=mock_client
    )

    assert first.id == second.id
    # Stripe API was only called once — second call hit the DB row.
    assert mock_client.customers.create.call_count == 1


@pytest.mark.asyncio
async def test_get_customer_by_workspace_returns_none_when_absent(
    session: AsyncSession,
) -> None:
    result = await billing_services.get_customer_by_workspace(session, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# 2. Subscription CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_or_create_subscription_creates_row(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    sub = await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.STARTER,
        status=SubscriptionStatus.TRIALING,
        stripe_subscription_id="sub_xyz",
    )
    assert sub.workspace_id == workspace_id
    assert sub.plan == SubscriptionPlan.STARTER
    assert sub.status == SubscriptionStatus.TRIALING
    assert sub.stripe_subscription_id == "sub_xyz"


@pytest.mark.asyncio
async def test_get_or_create_subscription_is_idempotent(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    first = await billing_services.get_or_create_subscription(session, workspace_id)
    await session.flush()
    second = await billing_services.get_or_create_subscription(session, workspace_id)
    assert first.id == second.id


@pytest.mark.asyncio
async def test_get_subscription_returns_none_for_new_workspace(
    session: AsyncSession,
) -> None:
    result = await billing_services.get_subscription(session, uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# 3. Webhook event log helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_webhook_event_and_is_processed(session: AsyncSession) -> None:
    event_id = "evt_record_001"
    assert not await billing_services.is_event_already_processed(session, event_id)

    await billing_services.record_webhook_event(session, event_id, "invoice.paid", status="handled")
    await session.flush()

    assert await billing_services.is_event_already_processed(session, event_id)


# ---------------------------------------------------------------------------
# 4. apply_subscription_updated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_subscription_updated_changes_status(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    sub = await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.STARTER,
        status=SubscriptionStatus.TRIALING,
        stripe_subscription_id="sub_upd_001",
    )
    await session.flush()

    stripe_sub_obj: dict[str, Any] = {
        "id": "sub_upd_001",
        "status": "active",
        "current_period_end": 1_900_000_000,
        "items": {"data": [{"price": {"id": "price_starter"}}]},
    }
    result = await billing_services.apply_subscription_updated(session, stripe_sub_obj)

    assert result is not None
    assert result.id == sub.id
    assert result.status == SubscriptionStatus.ACTIVE
    assert result.stripe_price_id == "price_starter"
    assert result.payment_failed is False


@pytest.mark.asyncio
async def test_apply_subscription_updated_returns_none_for_unknown_sub(
    session: AsyncSession,
) -> None:
    result = await billing_services.apply_subscription_updated(
        session, {"id": "sub_does_not_exist", "status": "active", "items": {"data": []}}
    )
    assert result is None


@pytest.mark.asyncio
async def test_apply_subscription_updated_maps_past_due(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id="sub_pd_001",
    )
    await session.flush()

    result = await billing_services.apply_subscription_updated(
        session, {"id": "sub_pd_001", "status": "past_due", "items": {"data": []}}
    )
    assert result is not None
    assert result.status == SubscriptionStatus.PAST_DUE


# ---------------------------------------------------------------------------
# 5. apply_invoice_paid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_invoice_paid_activates_subscription(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    sub = await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        plan=SubscriptionPlan.STARTER,
        status=SubscriptionStatus.PAST_DUE,
        stripe_subscription_id="sub_paid_001",
    )
    sub.payment_failed = True
    await session.flush()

    stripe_invoice: dict[str, Any] = {
        "id": "in_001",
        "subscription": "sub_paid_001",
        "period_end": 1_900_000_000,
    }
    result = await billing_services.apply_invoice_paid(session, stripe_invoice)

    assert result is not None
    assert result.status == SubscriptionStatus.ACTIVE
    assert result.payment_failed is False


@pytest.mark.asyncio
async def test_apply_invoice_paid_returns_none_when_no_subscription_field(
    session: AsyncSession,
) -> None:
    result = await billing_services.apply_invoice_paid(session, {"id": "in_no_sub"})
    assert result is None


# ---------------------------------------------------------------------------
# 6. apply_invoice_payment_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_invoice_payment_failed_sets_past_due(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        status=SubscriptionStatus.ACTIVE,
        stripe_subscription_id="sub_fail_001",
    )
    await session.flush()

    stripe_invoice: dict[str, Any] = {
        "id": "in_fail_001",
        "subscription": "sub_fail_001",
    }
    result = await billing_services.apply_invoice_payment_failed(session, stripe_invoice)

    assert result is not None
    assert result.payment_failed is True
    assert result.status == SubscriptionStatus.PAST_DUE


@pytest.mark.asyncio
async def test_payment_failed_does_not_downgrade_suspended(session: AsyncSession) -> None:
    workspace_id = uuid.uuid4()
    await billing_services.get_or_create_subscription(
        session,
        workspace_id,
        status=SubscriptionStatus.SUSPENDED,
        stripe_subscription_id="sub_susp_001",
    )
    await session.flush()

    result = await billing_services.apply_invoice_payment_failed(
        session, {"id": "in_x", "subscription": "sub_susp_001"}
    )
    assert result is not None
    # Status stays SUSPENDED — we do not further downgrade an already-suspended sub.
    assert result.status == SubscriptionStatus.SUSPENDED
    assert result.payment_failed is True


# ---------------------------------------------------------------------------
# 7. Webhook HTTP endpoint — signature verification
# ---------------------------------------------------------------------------

WEBHOOK_URL = "/api/v1/billing/webhook"
TEST_WEBHOOK_SECRET = "whsec_test_abc"


def _make_webhook_post_kwargs(event: dict[str, Any]) -> dict[str, Any]:
    payload, sig_header = signed_webhook_request(event, secret=TEST_WEBHOOK_SECRET)
    return {
        "content": payload,
        "headers": {
            "content-type": "application/json",
            "stripe-signature": sig_header,
        },
    }


def test_webhook_valid_signature_returns_200(client: Any) -> None:
    """A correctly signed webhook returns 200."""
    event = make_subscription_updated_event(
        stripe_subscription_id="sub_http_001", event_id="evt_http_001"
    )
    kwargs = _make_webhook_post_kwargs(event)

    with (
        patch(
            "civicsignals_api.modules.billing.routes._get_webhook_secret",
            return_value=TEST_WEBHOOK_SECRET,
        ),
        patch(
            "stripe.Webhook.construct_event",
            return_value=event,
        ),
    ):
        resp = client.post(WEBHOOK_URL, **kwargs)

    assert resp.status_code == 200


def test_webhook_invalid_signature_returns_400(client: Any) -> None:
    """An incorrectly signed webhook returns 400."""
    import stripe as stripe_sdk

    event = make_subscription_updated_event(event_id="evt_badsig_001")
    payload, _ = signed_webhook_request(event, secret="wrong_secret")

    with (
        patch(
            "civicsignals_api.modules.billing.routes._get_webhook_secret",
            return_value=TEST_WEBHOOK_SECRET,
        ),
        patch(
            "stripe.Webhook.construct_event",
            side_effect=stripe_sdk.error.SignatureVerificationError(  # type: ignore[no-untyped-call]
                "bad sig", "sig_header"
            ),
        ),
    ):
        resp = client.post(
            WEBHOOK_URL,
            content=payload,
            headers={
                "content-type": "application/json",
                "stripe-signature": "t=1,v1=badhex",
            },
        )

    assert resp.status_code == 400


def test_webhook_missing_signature_header_returns_400(client: Any) -> None:
    """Missing stripe-signature header returns 400."""
    resp = client.post(
        WEBHOOK_URL,
        content=b'{"id":"evt_nosig"}',
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# 8. Idempotent event processing (same event id twice → one DB row)
# ---------------------------------------------------------------------------


def test_webhook_idempotent_same_event_id(client: Any) -> None:
    """Posting the same Stripe event id twice results in exactly one event log row."""
    event = make_invoice_paid_event(event_id="evt_idem_001")

    def _post() -> Any:
        return client.post(
            WEBHOOK_URL,
            **_make_webhook_post_kwargs(event),
        )

    with (
        patch(
            "civicsignals_api.modules.billing.routes._get_webhook_secret",
            return_value=TEST_WEBHOOK_SECRET,
        ),
        patch("stripe.Webhook.construct_event", return_value=event),
    ):
        resp1 = _post()
        resp2 = _post()

    assert resp1.status_code == 200
    assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# 9. Unknown event type is gracefully ignored
# ---------------------------------------------------------------------------


def test_webhook_unknown_event_type_ignored(client: Any) -> None:
    """An unrecognized event type returns 200 and is logged as 'ignored'."""
    event = {
        "id": "evt_unknown_001",
        "type": "completely.unknown.event",
        "data": {"object": {"id": "obj_001"}},
    }

    with (
        patch(
            "civicsignals_api.modules.billing.routes._get_webhook_secret",
            return_value=TEST_WEBHOOK_SECRET,
        ),
        patch("stripe.Webhook.construct_event", return_value=event),
    ):
        resp = client.post(WEBHOOK_URL, **_make_webhook_post_kwargs(event))

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 10. Each of the 3 handled event types updates subscription state via HTTP
# ---------------------------------------------------------------------------


def _post_webhook(client: Any, event: dict[str, Any]) -> Any:
    with (
        patch(
            "civicsignals_api.modules.billing.routes._get_webhook_secret",
            return_value=TEST_WEBHOOK_SECRET,
        ),
        patch("stripe.Webhook.construct_event", return_value=event),
    ):
        return client.post(WEBHOOK_URL, **_make_webhook_post_kwargs(event))


def test_webhook_subscription_updated_event_returns_200(client: Any) -> None:
    event = make_subscription_updated_event(event_id="evt_sub_http")
    resp = _post_webhook(client, event)
    assert resp.status_code == 200


def test_webhook_invoice_paid_event_returns_200(client: Any) -> None:
    event = make_invoice_paid_event(event_id="evt_paid_http")
    resp = _post_webhook(client, event)
    assert resp.status_code == 200


def test_webhook_invoice_payment_failed_event_returns_200(client: Any) -> None:
    event = make_invoice_payment_failed_event(event_id="evt_fail_http")
    resp = _post_webhook(client, event)
    assert resp.status_code == 200
