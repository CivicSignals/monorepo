"""Pydantic request/response shapes for the billing module (doc 06 §3).

N1: Stripe customer + subscription wiring output schemas.
N2: Plan definition + workspace plan summary output schemas.
N3: Usage metering output schemas (current-period usage vs plan limits).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel

from civicsignals_api.modules.billing.models import SubscriptionPlan, SubscriptionStatus
from civicsignals_api.modules.billing.plans import Dimension, Feature, PlanDefinition


class BillingCustomerOut(BaseModel):
    """Response schema for a billing_customer row."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    workspace_id: uuid.UUID
    stripe_customer_id: str
    created_at: datetime
    updated_at: datetime


class BillingSubscriptionOut(BaseModel):
    """Response schema for a billing_subscription row."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    workspace_id: uuid.UUID
    plan: SubscriptionPlan
    status: SubscriptionStatus
    stripe_subscription_id: str | None
    stripe_price_id: str | None
    seats: int
    payment_failed: bool
    trial_ends_at: datetime | None
    current_period_end: datetime | None
    created_at: datetime
    updated_at: datetime


class PlanLimitsOut(BaseModel):
    """Serializable limits map for a plan (N2).

    ``None`` means unlimited for that dimension.
    """

    seats: int | None
    tracked_entities: int | None
    smart_searches_per_month: int | None
    contact_exports_per_month: int | None
    saved_searches: int | None
    api_requests_per_month: int | None
    ai_runs_per_month: int | None  # N3: LLM gateway calls per month

    @classmethod
    def from_definition(cls, defn: PlanDefinition) -> PlanLimitsOut:
        from civicsignals_api.modules.billing.plans import plan_limit

        return cls(
            seats=plan_limit(defn.plan, Dimension.SEATS),
            tracked_entities=plan_limit(defn.plan, Dimension.TRACKED_ENTITIES),
            smart_searches_per_month=plan_limit(defn.plan, Dimension.SMART_SEARCHES_PER_MONTH),
            contact_exports_per_month=plan_limit(defn.plan, Dimension.CONTACT_EXPORTS_PER_MONTH),
            saved_searches=plan_limit(defn.plan, Dimension.SAVED_SEARCHES),
            api_requests_per_month=plan_limit(defn.plan, Dimension.API_REQUESTS_PER_MONTH),
            ai_runs_per_month=plan_limit(defn.plan, Dimension.AI_RUNS_PER_MONTH),
        )


class PlanOut(BaseModel):
    """Full plan definition response (N2).

    Returned by ``GET /billing/plan`` so the frontend and SDKs can render
    upgrade CTAs and enforce soft limits client-side.
    """

    plan: SubscriptionPlan
    display_name: str
    features: list[str]  # Feature enum values enabled on this plan
    limits: PlanLimitsOut

    @classmethod
    def from_definition(cls, defn: PlanDefinition) -> PlanOut:
        return cls(
            plan=defn.plan,
            display_name=defn.display_name,
            features=sorted(f.value for f in defn.features),
            limits=PlanLimitsOut.from_definition(defn),
        )


class WorkspacePlanOut(BaseModel):
    """Effective plan for the current workspace (N2).

    ``GET /billing/plan`` response — the plan the workspace is *currently on*,
    resolved by ``billing.services.get_workspace_plan``.  Includes the full
    limits + features so clients have a single call for all gating decisions.
    """

    workspace_id: uuid.UUID
    effective_plan: PlanOut
    all_features: list[str] = sorted(f.value for f in Feature)


# ---------------------------------------------------------------------------
# N3: Usage metering output schemas
# ---------------------------------------------------------------------------


class DimensionUsageOut(BaseModel):
    """Usage vs limit for a single metering dimension (N3).

    ``used`` is the counter for the current period.
    ``limit`` is the plan cap (``None`` = unlimited).
    ``pct_used`` is ``used / limit * 100`` rounded to one decimal, or
    ``None`` when the limit is unlimited.

    # TODO N4: ``pct_used >= 80`` triggers the soft-limit banner;
    #   ``pct_used >= 100`` triggers the 429 hard-limit enforcement.
    """

    dimension: str
    used: int
    limit: int | None
    pct_used: float | None


class WorkspaceUsageOut(BaseModel):
    """Current-period usage vs plan limits for the workspace (N3).

    Returned by ``GET /billing/usage``.  The ``period`` is the billing month
    in ``YYYY-MM`` format (calendar month, UTC).  ``dimensions`` is a mapping
    of :class:`Dimension` value → :class:`DimensionUsageOut` so clients can
    look up any dimension by name without position-coupling.

    # TODO N4: N4 reads the same ``record_usage`` / ``get_usage`` calls and
    #   raises 429 when ``pct_used >= 100`` for metered dimensions.
    """

    workspace_id: uuid.UUID
    period: date  # first day of the billing month (YYYY-MM-01)
    dimensions: dict[str, DimensionUsageOut]
