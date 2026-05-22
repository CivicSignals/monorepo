"""b2 auth_oauth_identity table for Google OAuth account linking.

Revision ID: b2a3c4d5e6f7
Revises: 183ff61f2dfd
Create Date: 2026-05-22 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b2a3c4d5e6f7"
down_revision: str | None = "183ff61f2dfd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auth_oauth_identity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("provider_email", sa.String(length=255), nullable=False),
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
            ["user_id"],
            ["accounts_user.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider",
            "subject",
            name="uq_auth_oauth_identity_provider_subject",
        ),
        sa.UniqueConstraint(
            "provider",
            "user_id",
            name="uq_auth_oauth_identity_provider_user",
        ),
    )
    op.create_index(
        op.f("ix_auth_oauth_identity_provider"),
        "auth_oauth_identity",
        ["provider"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_oauth_identity_subject"),
        "auth_oauth_identity",
        ["subject"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_oauth_identity_user_id"),
        "auth_oauth_identity",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_oauth_identity_user_id"), table_name="auth_oauth_identity")
    op.drop_index(op.f("ix_auth_oauth_identity_subject"), table_name="auth_oauth_identity")
    op.drop_index(op.f("ix_auth_oauth_identity_provider"), table_name="auth_oauth_identity")
    op.drop_table("auth_oauth_identity")
