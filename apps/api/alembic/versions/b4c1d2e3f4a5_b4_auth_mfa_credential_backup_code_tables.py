"""b4 auth_mfa_credential and auth_mfa_backup_code tables for TOTP MFA.

Revision ID: b4c1d2e3f4a5
Revises: 42a17cb82fe1
Create Date: 2026-05-22 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b4c1d2e3f4a5"
down_revision: str | None = "42a17cb82fe1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # auth_mfa_credential — one row per user; holds the Fernet-encrypted TOTP
    # secret and the activation flag (B4).
    op.create_table(
        "auth_mfa_credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("totp_secret_encrypted", sa.String(), nullable=False),
        sa.Column("activated", sa.Boolean(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("user_id", name="uq_auth_mfa_credential_user"),
    )
    op.create_index(
        op.f("ix_auth_mfa_credential_user_id"),
        "auth_mfa_credential",
        ["user_id"],
        unique=False,
    )

    # auth_mfa_backup_code — one row per code (SHA-256 hashed); cascades from
    # auth_mfa_credential and accounts_user (B4).
    op.create_table(
        "auth_mfa_backup_code",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mfa_credential_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code_hash", sa.String(), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["mfa_credential_id"],
            ["auth_mfa_credential.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["accounts_user.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index(
        op.f("ix_auth_mfa_backup_code_code_hash"),
        "auth_mfa_backup_code",
        ["code_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_auth_mfa_backup_code_mfa_credential_id"),
        "auth_mfa_backup_code",
        ["mfa_credential_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auth_mfa_backup_code_user_id"),
        "auth_mfa_backup_code",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_auth_mfa_backup_code_user_id"), table_name="auth_mfa_backup_code")
    op.drop_index(
        op.f("ix_auth_mfa_backup_code_mfa_credential_id"), table_name="auth_mfa_backup_code"
    )
    op.drop_index(op.f("ix_auth_mfa_backup_code_code_hash"), table_name="auth_mfa_backup_code")
    op.drop_table("auth_mfa_backup_code")
    op.drop_index(op.f("ix_auth_mfa_credential_user_id"), table_name="auth_mfa_credential")
    op.drop_table("auth_mfa_credential")
