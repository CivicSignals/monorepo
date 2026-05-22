"""billing SQLAlchemy models.

Tables are prefixed ``billing_`` and are migrated only by this module
(doc 06 §3, §4).

N1: Stripe customer + subscription wiring (this task).
  - billing_customer: one-to-one link workspace ↔ Stripe customer id.
  - billing_subscription: per-workspace subscription state (driven by Stripe events).
  - billing_webhook_event: idempotency/event-log for processed Stripe webhook events.

Future tasks:
  # N2: Plan definitions are code-defined in billing/plans.py (no DB table needed).
  # TODO N3: billing_usage for metered events (smart searches, exports) extends here.
  # TODO N4: billing_limit rows (per-plan caps) reference billing_subscription.
  # TODO N5: self-serve checkout + plan-change flow uses billing_customer.stripe_customer_id.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class SubscriptionStatus(StrEnum):
    """Lifecycle states for a workspace subscription (doc 07 §4 Subscription).

    ``trialing → active → past_due → read_only → suspended → cancelled``
    """

    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    READ_ONLY = "read_only"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class SubscriptionPlan(StrEnum):
    """Plans available in the system (doc 07 §2, PRD F15).

    # TODO N2: replace/extend with a richer billing_plan table (price IDs, feature
    #   flags, seat limits) once plan definitions land.
    """

    SELF_HOSTED = "self_hosted"
    SOLO = "solo"
    STARTER = "starter"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class BillingCustomer(Base):
    """One-to-one link from an ``accounts_workspace`` to a Stripe customer (N1).

    The Stripe customer is created lazily on the first billing action (checkout,
    subscription create). Billing is per-workspace (B5 spec): a workspace maps
    directly to a Stripe customer, not to an organization.

    ``stripe_customer_id`` is the Stripe ``cus_…`` id. It is unique so we cannot
    accidentally associate two workspaces with the same Stripe customer.

    # TODO N5: attach ``default_payment_method_id`` (Stripe pm_…) here when the
    #   self-serve payment-method-update flow (N5) lands.
    # TODO LC-13: STRIPE_SECRET_KEY / live keys provisioned by LC-13 (external Stripe
    #   account). The column is present now so the migration ships once.
    """

    __tablename__ = "billing_customer"
    __table_args__ = (
        UniqueConstraint("workspace_id", name="uq_billing_customer_workspace"),
        UniqueConstraint("stripe_customer_id", name="uq_billing_customer_stripe_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stripe_customer_id: Mapped[str] = mapped_column(String, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BillingSubscription(Base):
    """Per-workspace subscription state, kept in sync by Stripe webhooks (N1).

    ``stripe_subscription_id`` is nullable because a workspace on the
    ``self_hosted`` or trial plan may never have a Stripe subscription.
    ``current_period_end`` mirrors the Stripe subscription's ``current_period_end``
    and is updated on every ``customer.subscription.updated`` / ``invoice.paid``
    event.

    One subscription per workspace (UNIQUE on ``workspace_id``). Other modules
    (N4 limits, N5 self-serve) read ``status`` + ``plan`` directly from this row
    via ``billing.services``.

    # N2: feature flags + limits are code-defined in billing/plans.py (no JSONB column needed).
    # TODO N4: billing limits (smart-search quota, export quota) read .plan + .status.
    # TODO N5: self-serve plan-change POSTs through billing.services.change_plan().
    # TODO LC-13: live Stripe subscription IDs populated after LC-13 (real account).
    """

    __tablename__ = "billing_subscription"
    __table_args__ = (UniqueConstraint("workspace_id", name="uq_billing_subscription_workspace"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    plan: Mapped[SubscriptionPlan] = mapped_column(
        Enum(
            SubscriptionPlan,
            name="billing_subscription_plan",
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=SubscriptionPlan.SOLO.value,
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(
            SubscriptionStatus,
            name="billing_subscription_status",
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=SubscriptionStatus.TRIALING.value,
    )

    # Stripe subscription + price ids (nullable — absent for self_hosted / trialing).
    stripe_subscription_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    stripe_price_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Trial + period bookkeeping.
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Seat count (used by N4 limits).
    seats: Mapped[int] = mapped_column(nullable=False, server_default="1")

    # True while the last invoice payment attempt failed (drives UI warnings).
    payment_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class BillingWebhookEvent(Base):
    """Idempotency log of processed Stripe webhook events (N1).

    Every Stripe event processed by ``POST /billing/webhook`` is written here
    (keyed on ``stripe_event_id``). Re-delivery of the same event id is detected
    before any state mutation and returns 200 immediately.

    ``processed_at`` records when we first handled the event; ``event_type``
    mirrors the Stripe ``type`` field (e.g. ``invoice.paid``) so operators can
    query the log by event type; ``status`` captures whether we handled it or
    ignored it (unknown type → ``ignored``).
    """

    __tablename__ = "billing_webhook_event"
    __table_args__ = (
        UniqueConstraint("stripe_event_id", name="uq_billing_webhook_event_stripe_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    stripe_event_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # "handled" | "ignored" | "error"
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="handled")
    # Human-readable note (e.g. "unknown event type" for ignored events).
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
