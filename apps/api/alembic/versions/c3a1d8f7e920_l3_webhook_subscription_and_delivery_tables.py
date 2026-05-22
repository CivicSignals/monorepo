"""l3 webhook subscription and delivery tables

Revision ID: c3a1d8f7e920
Revises: b282915c8b8f
Create Date: 2026-05-22 14:00:00.000000

L3: adds ``integrations_webhook_subscription`` and ``integrations_webhook_delivery``
tables for the outbound-webhook subscriber CRUD + delivery log feature.

Only touches ``integrations_`` prefixed tables (this module's responsibility per
doc 06 §3). The ``integrations_webhook_delivery_status`` enum is created inline
(non-native, stored as varchar). The existing ``integrations_`` Enum types are
left untouched (no alter of existing tables).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a1d8f7e920"
down_revision: str | tuple[str, ...] | None = "b282915c8b8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- integrations_webhook_subscription -----------------------------------
    op.create_table(
        "integrations_webhook_subscription",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "subscribed_events",
            sa.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["created_by_user_id"],
            ["accounts_user.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["accounts_workspace.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integrations_webhook_subscription_workspace",
        "integrations_webhook_subscription",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_webhook_subscription_workspace_active",
        "integrations_webhook_subscription",
        ["workspace_id", "active"],
        unique=False,
    )

    # --- integrations_webhook_delivery ---------------------------------------
    op.create_table(
        "integrations_webhook_delivery",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column(
            "request_body",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "request_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "success",
                "failed",
                "dead_letter",
                name="integrations_webhook_delivery_status",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=True),
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
            ["subscription_id"],
            ["integrations_webhook_subscription.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["accounts_workspace.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integrations_webhook_delivery_subscription",
        "integrations_webhook_delivery",
        ["subscription_id"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_webhook_delivery_workspace",
        "integrations_webhook_delivery",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_webhook_delivery_status",
        "integrations_webhook_delivery",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_webhook_delivery_retry",
        "integrations_webhook_delivery",
        ["status", "retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_webhook_delivery_event_id",
        "integrations_webhook_delivery",
        ["event_id"],
        unique=False,
    )


def downgrade() -> None:
    # Remove indexes then tables in reverse dependency order.
    op.drop_index(
        "ix_integrations_webhook_delivery_event_id",
        table_name="integrations_webhook_delivery",
    )
    op.drop_index(
        "ix_integrations_webhook_delivery_retry",
        table_name="integrations_webhook_delivery",
    )
    op.drop_index(
        "ix_integrations_webhook_delivery_status",
        table_name="integrations_webhook_delivery",
    )
    op.drop_index(
        "ix_integrations_webhook_delivery_workspace",
        table_name="integrations_webhook_delivery",
    )
    op.drop_index(
        "ix_integrations_webhook_delivery_subscription",
        table_name="integrations_webhook_delivery",
    )
    op.drop_table("integrations_webhook_delivery")

    op.drop_index(
        "ix_integrations_webhook_subscription_workspace_active",
        table_name="integrations_webhook_subscription",
    )
    op.drop_index(
        "ix_integrations_webhook_subscription_workspace",
        table_name="integrations_webhook_subscription",
    )
    op.drop_table("integrations_webhook_subscription")
