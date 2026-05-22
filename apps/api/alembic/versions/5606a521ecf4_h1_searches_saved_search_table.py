"""H1 searches saved_search table

Revision ID: 5606a521ecf4
Revises: d92401cfa555
Create Date: 2026-05-22 17:53:42.208198
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5606a521ecf4"
down_revision: str | None = "d92401cfa555"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "searches_saved_search",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "filters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_shared", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.ForeignKeyConstraint(["created_by"], ["accounts_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_searches_saved_search_created_by",
        "searches_saved_search",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        "ix_searches_saved_search_workspace_id",
        "searches_saved_search",
        ["workspace_id"],
        unique=False,
    )
    # Partial index serving the "shared searches visible to the workspace" read.
    op.create_index(
        "ix_searches_saved_search_workspace_shared",
        "searches_saved_search",
        ["workspace_id"],
        unique=False,
        postgresql_where=sa.text("is_shared"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_searches_saved_search_workspace_shared",
        table_name="searches_saved_search",
        postgresql_where=sa.text("is_shared"),
    )
    op.drop_index("ix_searches_saved_search_workspace_id", table_name="searches_saved_search")
    op.drop_index("ix_searches_saved_search_created_by", table_name="searches_saved_search")
    op.drop_table("searches_saved_search")
