"""n1_billing_customer_subscription_webhook

N1: Stripe customer + subscription wiring.

Creates three billing_* tables:
  - billing_customer: workspace ↔ Stripe customer id (one-to-one).
  - billing_subscription: per-workspace subscription state (status, plan, Stripe ids).
  - billing_webhook_event: idempotency log of processed Stripe webhook events.

Revision ID: a9f3e2b1c4d5
Revises: 467a515ea5a6
Create Date: 2026-05-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9f3e2b1c4d5"
down_revision: str | None = "467a515ea5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # billing_customer — workspace ↔ Stripe customer id (one-to-one).
    # ------------------------------------------------------------------
    op.create_table(
        "billing_customer",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["accounts_workspace.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_billing_customer_workspace"),
        sa.UniqueConstraint("stripe_customer_id", name="uq_billing_customer_stripe_id"),
    )
    op.create_index(
        op.f("ix_billing_customer_workspace_id"),
        "billing_customer",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_customer_stripe_customer_id"),
        "billing_customer",
        ["stripe_customer_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # billing_subscription — per-workspace subscription state.
    # ------------------------------------------------------------------
    op.create_table(
        "billing_subscription",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "plan",
            sa.String(length=32),
            server_default="solo",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="trialing",
            nullable=False,
        ),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("stripe_price_id", sa.String(), nullable=True),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("seats", sa.Integer(), server_default="1", nullable=False),
        sa.Column("payment_failed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["accounts_workspace.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", name="uq_billing_subscription_workspace"),
    )
    op.create_index(
        op.f("ix_billing_subscription_workspace_id"),
        "billing_subscription",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_billing_subscription_stripe_subscription_id"),
        "billing_subscription",
        ["stripe_subscription_id"],
        unique=False,
    )

    # ------------------------------------------------------------------
    # billing_webhook_event — idempotency log for Stripe webhook events.
    # ------------------------------------------------------------------
    op.create_table(
        "billing_webhook_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("stripe_event_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="handled", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("stripe_event_id", name="uq_billing_webhook_event_stripe_id"),
    )
    op.create_index(
        op.f("ix_billing_webhook_event_stripe_event_id"),
        "billing_webhook_event",
        ["stripe_event_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_billing_webhook_event_event_type"),
        "billing_webhook_event",
        ["event_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_billing_webhook_event_event_type"),
        table_name="billing_webhook_event",
    )
    op.drop_index(
        op.f("ix_billing_webhook_event_stripe_event_id"),
        table_name="billing_webhook_event",
    )
    op.drop_table("billing_webhook_event")

    op.drop_index(
        op.f("ix_billing_subscription_stripe_subscription_id"),
        table_name="billing_subscription",
    )
    op.drop_index(
        op.f("ix_billing_subscription_workspace_id"),
        table_name="billing_subscription",
    )
    op.drop_table("billing_subscription")

    op.drop_index(
        op.f("ix_billing_customer_stripe_customer_id"),
        table_name="billing_customer",
    )
    op.drop_index(
        op.f("ix_billing_customer_workspace_id"),
        table_name="billing_customer",
    )
    op.drop_table("billing_customer")
