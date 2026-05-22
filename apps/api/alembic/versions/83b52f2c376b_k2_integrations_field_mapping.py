"""k2 integrations field mapping

Revision ID: 83b52f2c376b
Revises: e908c20ae8c7
Create Date: 2026-05-22 12:19:59.004767
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "83b52f2c376b"
down_revision: str | None = "e908c20ae8c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrations_field_mapping",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("target_object", sa.String(length=255), nullable=False),
        sa.Column(
            "field_map",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "constants",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
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
            ["connection_id"], ["integrations_connection.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integrations_field_mapping_connection",
        "integrations_field_mapping",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_field_mapping_workspace",
        "integrations_field_mapping",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_integrations_field_mapping_connection_target",
        "integrations_field_mapping",
        ["connection_id", "target_object"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_integrations_field_mapping_connection_target",
        table_name="integrations_field_mapping",
    )
    op.drop_index(
        "ix_integrations_field_mapping_workspace", table_name="integrations_field_mapping"
    )
    op.drop_index(
        "ix_integrations_field_mapping_connection", table_name="integrations_field_mapping"
    )
    op.drop_table("integrations_field_mapping")
