"""k6 integrations field mapping template

Revision ID: 1423dd9eb65a
Revises: d92401cfa555
Create Date: 2026-05-23 00:19:32.600405
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1423dd9eb65a"
down_revision: str | None = "d92401cfa555"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrations_field_mapping_template",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
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
        sa.Column("is_default", sa.Boolean(), server_default="false", nullable=False),
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
        "ix_integrations_field_mapping_template_connection",
        "integrations_field_mapping_template",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_field_mapping_template_workspace",
        "integrations_field_mapping_template",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "uq_integrations_field_mapping_template_connection_name",
        "integrations_field_mapping_template",
        ["connection_id", "name"],
        unique=True,
    )
    # At most one default template per connection (partial unique index).
    op.create_index(
        "uq_integrations_field_mapping_template_connection_default",
        "integrations_field_mapping_template",
        ["connection_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_integrations_field_mapping_template_connection_default",
        table_name="integrations_field_mapping_template",
        postgresql_where=sa.text("is_default"),
    )
    op.drop_index(
        "uq_integrations_field_mapping_template_connection_name",
        table_name="integrations_field_mapping_template",
    )
    op.drop_index(
        "ix_integrations_field_mapping_template_workspace",
        table_name="integrations_field_mapping_template",
    )
    op.drop_index(
        "ix_integrations_field_mapping_template_connection",
        table_name="integrations_field_mapping_template",
    )
    op.drop_table("integrations_field_mapping_template")
