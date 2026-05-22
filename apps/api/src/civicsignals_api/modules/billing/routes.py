"""HTTP endpoints for the billing module, mounted under ``/api/v1/billing``.

N1 implements:
  - ``POST /billing/webhook``  — Stripe webhook receiver.
  - ``GET  /billing/subscription`` — current workspace subscription (authenticated).
  - ``GET  /billing/customer``     — current workspace Stripe customer link.
  - ``POST /billing/customer``     — create a Stripe customer for the workspace.

The webhook endpoint is unauthenticated (verified by Stripe signature). All
other endpoints use ``require_workspace`` (B5).

# TODO N5: add POST /billing/checkout-session (self-serve plan sign-up).
# TODO N5: add POST /billing/plan (plan-change / upgrade / downgrade).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import stripe as stripe_sdk
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import CurrentWorkspace
from civicsignals_api.modules.billing import services
from civicsignals_api.modules.billing.models import BillingCustomer, BillingSubscription
from civicsignals_api.modules.billing.schemas import (
    BillingCustomerOut,
    BillingSubscriptionOut,
)
from civicsignals_api.problems import ProblemException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_webhook_secret() -> str:
    from civicsignals_api.config import get_settings

    secret = get_settings().stripe_webhook_secret
    if not secret:
        raise ProblemException(
            status=503,
            code="service_unavailable",
            title="Billing not configured",
            detail="STRIPE_WEBHOOK_SECRET is not set.",
        )
    return secret


# ---------------------------------------------------------------------------
# Webhook receiver
# ---------------------------------------------------------------------------


@router.post(
    "/webhook",
    status_code=200,
    summary="Stripe webhook receiver",
    description=(
        "Receives Stripe events, verifies the signature, deduplicates via the "
        "billing_webhook_event log, and dispatches to the relevant handler. "
        "Returns 200 immediately for unknown event types (Stripe expects 200 for "
        "all deliveries; errors return 4xx/5xx which triggers Stripe retries)."
    ),
    include_in_schema=False,  # Stripe webhooks are not part of the public API schema
)
async def stripe_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    stripe_signature: Annotated[str | None, Header(alias="stripe-signature")] = None,
) -> Response:
    """Verify Stripe signature, deduplicate, and dispatch event handlers.

    Returns 200 for all successfully processed or gracefully ignored events.
    Returns 400 if the signature is invalid (Stripe will retry on 5xx, not 4xx,
    so we return 400 to permanently reject bad requests without clogging retries).
    """
    if stripe_signature is None:
        raise ProblemException(
            status=400,
            code="bad_request",
            title="Missing Stripe signature",
            detail="stripe-signature header is required.",
        )

    payload = await request.body()
    webhook_secret = _get_webhook_secret()

    try:
        event: Any = stripe_sdk.Webhook.construct_event(  # type: ignore[no-untyped-call]
            payload=payload,
            sig_header=stripe_signature,
            secret=webhook_secret,
        )
    except stripe_sdk.error.SignatureVerificationError as exc:
        logger.warning("Stripe webhook signature verification failed: %s", exc)
        raise ProblemException(
            status=400,
            code="bad_request",
            title="Invalid Stripe signature",
            detail="The stripe-signature header could not be verified.",
        ) from exc
    except Exception as exc:
        logger.error("Stripe Webhook.construct_event raised unexpectedly: %s", exc)
        raise ProblemException(
            status=400,
            code="bad_request",
            title="Malformed webhook payload",
            detail="Could not parse Stripe event payload.",
        ) from exc

    event_id: str = event["id"]
    event_type: str = event["type"]
    event_data: dict[str, Any] = event["data"]["object"]

    async with session.begin():
        # Idempotency check — if we've already handled this event, return early.
        if await services.is_event_already_processed(session, event_id):
            logger.debug("Duplicate Stripe event %s (%s) — skipping", event_id, event_type)
            return Response(status_code=200, content=b"", media_type="application/json")

        # Dispatch to the appropriate handler.
        if event_type == "customer.subscription.updated":
            await services.apply_subscription_updated(session, event_data)
            await services.record_webhook_event(session, event_id, event_type, status="handled")
        elif event_type == "invoice.paid":
            await services.apply_invoice_paid(session, event_data)
            await services.record_webhook_event(session, event_id, event_type, status="handled")
        elif event_type == "invoice.payment_failed":
            await services.apply_invoice_payment_failed(session, event_data)
            await services.record_webhook_event(session, event_id, event_type, status="handled")
        else:
            # Unknown / unhandled event type — log as ignored, return 200 so
            # Stripe does not retry.
            await services.record_webhook_event(
                session,
                event_id,
                event_type,
                status="ignored",
                note=f"unhandled event type: {event_type}",
            )
            logger.debug("Ignored Stripe event type %s (%s)", event_type, event_id)

    return Response(status_code=200, content=b"{}", media_type="application/json")


# ---------------------------------------------------------------------------
# Authenticated billing endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/subscription",
    response_model=BillingSubscriptionOut | None,
    summary="Get current workspace subscription",
)
async def get_subscription(
    ctx: CurrentWorkspace,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BillingSubscription | None:
    """Return the billing subscription for the current workspace (or null)."""
    return await services.get_subscription(session, ctx.workspace_id)


@router.get(
    "/customer",
    response_model=BillingCustomerOut | None,
    summary="Get current workspace Stripe customer link",
)
async def get_customer(
    ctx: CurrentWorkspace,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BillingCustomer | None:
    """Return the Stripe customer link for the current workspace (or null)."""
    return await services.get_customer_by_workspace(session, ctx.workspace_id)


@router.post(
    "/customer",
    response_model=BillingCustomerOut,
    status_code=201,
    summary="Create Stripe customer for the current workspace",
)
async def create_customer(
    ctx: CurrentWorkspace,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BillingCustomer:
    """Create (or return existing) Stripe customer for the current workspace.

    Idempotent — calling this multiple times returns the same row.
    Requires ``STRIPE_SECRET_KEY`` to be configured.

    # TODO N5: pass email/name from the workspace owner for a better Stripe
    #   dashboard experience once self-serve checkout lands.
    """
    async with session.begin():
        customer = await services.get_or_create_customer(
            session,
            workspace_id=ctx.workspace_id,
            email=ctx.user.email,
            name=ctx.user.name or ctx.workspace.name,
        )
    return customer
