"""l1 integrations_slack_channel table

L1: Slack OAuth + channel selection.

Creates ``integrations_slack_channel`` — one row per Slack
:class:`~civicsignals_api.modules.integrations.models.Connection` holding the
admin's selected notification channel id + display name.  The row is cascade-
deleted when the parent connection is disconnected.

Revision ID: b1a2c3d4e5f6
Revises: d7c6c4b3e3d1
Create Date: 2026-05-22 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1a2c3d4e5f6"
down_revision: str | None = "d7c6c4b3e3d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # integrations_slack_channel — per-connection Slack channel selection.
    # ------------------------------------------------------------------
    op.create_table(
        "integrations_slack_channel",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("channel_id", sa.String(length=64), nullable=False),
        sa.Column("channel_name", sa.String(length=255), nullable=False),
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
            ["connection_id"],
            ["integrations_connection.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["accounts_workspace.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("connection_id", name="uq_integrations_slack_channel_connection"),
    )
    op.create_index(
        "ix_integrations_slack_channel_connection",
        "integrations_slack_channel",
        ["connection_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_integrations_slack_channel_connection",
        table_name="integrations_slack_channel",
    )
    op.drop_table("integrations_slack_channel")
