"""Pydantic request/response shapes for the billing module (doc 06 §3).

N1: Stripe customer + subscription wiring output schemas.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from civicsignals_api.modules.billing.models import SubscriptionPlan, SubscriptionStatus


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
