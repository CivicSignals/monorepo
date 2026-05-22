"""b6_invitation_table

Revision ID: 68e4c0a07d07
Revises: 378de8542902
Create Date: 2026-05-22 10:55:19.643497

B6 — add accounts_invitation table (hashed single-use invite tokens).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "68e4c0a07d07"
down_revision: str | None = "378de8542902"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts_invitation",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("invited_email", postgresql.CITEXT(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "owner",
                "admin",
                "member",
                "viewer",
                name="accounts_membership_role",
                native_enum=False,
                length=16,
            ),
            server_default="member",
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "accepted",
                "revoked",
                "expired",
                name="accounts_invitation_status",
                native_enum=False,
                length=16,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("invited_by", sa.UUID(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["invited_by"], ["accounts_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_accounts_invitation_invited_by"),
        "accounts_invitation",
        ["invited_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_accounts_invitation_invited_email"),
        "accounts_invitation",
        ["invited_email"],
        unique=False,
    )
    op.create_index(
        op.f("ix_accounts_invitation_status"),
        "accounts_invitation",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_accounts_invitation_token_hash"),
        "accounts_invitation",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_accounts_invitation_workspace_id"),
        "accounts_invitation",
        ["workspace_id"],
        unique=False,
    )
    # Partial unique index: one pending invite per (workspace, email).
    op.create_index(
        "uq_accounts_invitation_pending",
        "accounts_invitation",
        ["workspace_id", "invited_email"],
        unique=True,
        postgresql_where="status = 'pending'",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_accounts_invitation_pending",
        table_name="accounts_invitation",
        postgresql_where="status = 'pending'",
    )
    op.drop_index(op.f("ix_accounts_invitation_workspace_id"), table_name="accounts_invitation")
    op.drop_index(op.f("ix_accounts_invitation_token_hash"), table_name="accounts_invitation")
    op.drop_index(op.f("ix_accounts_invitation_status"), table_name="accounts_invitation")
    op.drop_index(op.f("ix_accounts_invitation_invited_email"), table_name="accounts_invitation")
    op.drop_index(op.f("ix_accounts_invitation_invited_by"), table_name="accounts_invitation")
    op.drop_table("accounts_invitation")
