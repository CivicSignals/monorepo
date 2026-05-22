"""B9 admin audit event table.

Revision ID: c9f1a2b3d4e5
Revises: b532c18ae083
Create Date: 2026-05-22 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9f1a2b3d4e5"
down_revision: str | None = "b532c18ae083"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ### B9: append-only audit log (doc 07 §2 ``audit_event``) ###
    op.create_table(
        "admin_audit_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("actor_token_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Composite index covering workspace-scoped time-ordered queries (most common access pattern).
    op.create_index(
        "ix_admin_audit_event_workspace_time",
        "admin_audit_event",
        ["workspace_id", "occurred_at"],
        unique=False,
        postgresql_ops={"occurred_at": "DESC"},
    )
    # Individual filter indexes.
    op.create_index(
        "ix_admin_audit_event_action",
        "admin_audit_event",
        ["action"],
        unique=False,
    )
    op.create_index(
        "ix_admin_audit_event_actor_user_id",
        "admin_audit_event",
        ["actor_user_id"],
        unique=False,
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### B9: drop audit log ###
    op.drop_index("ix_admin_audit_event_actor_user_id", table_name="admin_audit_event")
    op.drop_index("ix_admin_audit_event_action", table_name="admin_audit_event")
    op.drop_index("ix_admin_audit_event_workspace_time", table_name="admin_audit_event")
    op.drop_table("admin_audit_event")
    # ### end Alembic commands ###
