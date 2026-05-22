"""HTTP endpoints for the billing module, mounted under ``/api/v1/billing``.

N1 implements:
  - ``POST /billing/webhook``  — Stripe webhook receiver.
  - ``GET  /billing/subscription`` — current workspace subscription (authenticated).
  - ``GET  /billing/customer``     — current workspace Stripe customer link.
  - ``POST /billing/customer``     — create a Stripe customer for the workspace.

N2 implements:
  - ``GET /billing/plan``       — effective plan + limits + feature flags for the workspace.
  - ``GET /billing/plan/demo``  — demo of a feature-gated endpoint (requires SMART_SEARCH).

N3 implements:
  - ``GET /billing/usage``      — current-period usage vs plan limits for the workspace.

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
from civicsignals_api.modules.billing.dependencies import require_feature
from civicsignals_api.modules.billing.models import BillingCustomer, BillingSubscription
from civicsignals_api.modules.billing.plans import Dimension, Feature, get_plan
from civicsignals_api.modules.billing.schemas import (
    BillingCustomerOut,
    BillingSubscriptionOut,
    DimensionUsageOut,
    PlanOut,
    WorkspacePlanOut,
    WorkspaceUsageOut,
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


# ---------------------------------------------------------------------------
# N2: Plan + feature-flag endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/plan",
    response_model=WorkspacePlanOut,
    summary="Get effective plan, limits, and feature flags for the current workspace",
    description=(
        "Returns the workspace's effective billing plan (resolved from "
        "``billing_subscription``; defaults to ``self_hosted`` when no active "
        "subscription exists), its numeric limits per metering dimension, and the "
        "set of features the plan enables. Clients use this to render upgrade CTAs "
        "and enforce soft limits without a Stripe call on the hot path (N2)."
    ),
)
async def get_workspace_plan(
    ctx: CurrentWorkspace,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspacePlanOut:
    """Return the effective plan + limits + feature flags for the current workspace."""
    effective_plan = await services.get_workspace_plan(session, ctx.workspace_id)
    defn = get_plan(effective_plan)
    return WorkspacePlanOut(
        workspace_id=ctx.workspace_id,
        effective_plan=PlanOut.from_definition(defn),
    )


@router.get(
    "/plan/demo-smart-search",
    response_model=dict[str, str],
    summary="[Demo] Feature-gated endpoint requiring SMART_SEARCH (N2)",
    description=(
        "Demonstration of the ``require_feature`` dependency pattern (N2). "
        "Returns 402 when the workspace plan does not include ``smart_search``. "
        "Production smart-search routes live in the ``smart_search`` module; "
        "this endpoint exists only to validate the gate in the billing module's "
        "test suite. "
        "# TODO N4: actual quota enforcement (runs/month cap) goes here once metering lands."
    ),
)
async def demo_smart_search_gate(
    ctx: CurrentWorkspace,
    session: Annotated[AsyncSession, Depends(get_session)],
    _gate: Annotated[None, Depends(require_feature(Feature.SMART_SEARCH))],
) -> dict[str, str]:
    """Return a stub response; the real value is the 402 gate on plans lacking SMART_SEARCH."""
    # TODO N4: enforce smart-search monthly quota before reaching here.
    return {"status": "ok", "plan": (await services.get_workspace_plan(session, ctx.workspace_id))}


# ---------------------------------------------------------------------------
# N3: Usage metering endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/usage",
    response_model=WorkspaceUsageOut,
    summary="Get current-period usage vs plan limits for the workspace",
    description=(
        "Returns the workspace's metered usage for the current billing month "
        "(calendar month, UTC), broken down by dimension, alongside the plan "
        "cap for each dimension (``null`` = unlimited). The ``seats`` dimension "
        "is refreshed on every call (live count from ``accounts_member``). All "
        "other dimensions reflect the accumulated counters in ``billing_usage``.\n\n"
        "Clients use this to render usage meters and upgrade CTAs. N4 will add "
        "enforcement (soft 80 % banner + hard 429 at 100 %) on top of these numbers."
        "# TODO N4: enforcement layer reads the same numbers returned here."
    ),
)
async def get_workspace_usage(
    ctx: CurrentWorkspace,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> WorkspaceUsageOut:
    """Return current-period usage vs plan limits for the requesting workspace (N3).

    Steps:
    1. Refresh the seats snapshot (live count from accounts_member).
    2. Load per-dimension usage counters from billing_usage.
    3. Resolve plan limits from N2's plan registry.
    4. Combine into a WorkspaceUsageOut with pct_used for each dimension.
    """
    period = services.current_period()

    # Refresh seats (snapshot, not accumulated).
    async with session.begin_nested():
        await services.refresh_seats_usage(session, ctx.workspace_id, period=period)

    # Read all usage counters for this workspace + period.
    usage_map = await services.get_usage(session, ctx.workspace_id, period=period)

    # Resolve the workspace plan limits (N2).
    effective_plan = await services.get_workspace_plan(session, ctx.workspace_id)

    # Build per-dimension usage-vs-limit objects.  We iterate over all known
    # Dimensions so every dimension appears in the response even with zero usage.
    dimensions: dict[str, DimensionUsageOut] = {}
    for dim in Dimension:
        used = usage_map.get(dim.value, 0)
        limit = services.plan_limit(effective_plan, dim)
        pct: float | None = None
        if limit is not None and limit > 0:
            pct = round(used / limit * 100, 1)
        dimensions[dim.value] = DimensionUsageOut(
            dimension=dim.value,
            used=used,
            limit=limit,
            pct_used=pct,
        )

    return WorkspaceUsageOut(
        workspace_id=ctx.workspace_id,
        period=period,
        dimensions=dimensions,
    )
