"""J3 pipeline_item_activity — append-only activity log for pipeline items.

Revision ID: e1f2a3b4c5d6
Revises: bb5285cd2571
Create Date: 2026-05-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "bb5285cd2571"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ### J3: append-only activity log for pipeline items ###
    op.create_table(
        "pipeline_item_activity",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column(
            "activity_type",
            sa.String(length=20),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["pipeline_item.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["accounts_workspace.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Composite index: item timeline queries (most common — GET /items/{id}/activity).
    op.create_index(
        "ix_pipeline_item_activity_item_time",
        "pipeline_item_activity",
        ["item_id", "created_at"],
        unique=False,
        postgresql_ops={"created_at": "DESC"},
    )
    # Composite index: workspace-scoped activity queries (less common, for admin views).
    op.create_index(
        "ix_pipeline_item_activity_workspace_time",
        "pipeline_item_activity",
        ["workspace_id", "created_at"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### J3: drop pipeline activity log ###
    op.drop_index(
        "ix_pipeline_item_activity_workspace_time",
        table_name="pipeline_item_activity",
    )
    op.drop_index(
        "ix_pipeline_item_activity_item_time",
        table_name="pipeline_item_activity",
    )
    op.drop_table("pipeline_item_activity")
    # ### end Alembic commands ###
