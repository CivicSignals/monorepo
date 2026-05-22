"""K1 integrations connection + push log

Revision ID: a298967820ff
Revises: cf8e83e2f234
Create Date: 2026-05-22 10:54:50.676476
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a298967820ff"
down_revision: str | None = "cf8e83e2f234"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrations_connection",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column(
            "provider",
            sa.Enum(
                "salesforce",
                "hubspot",
                "slack",
                "webhook",
                name="integrations_provider",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending_oauth",
                "healthy",
                "degraded",
                "needs_reauth",
                "revoked",
                name="integrations_connection_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scopes", sa.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column(
            "provider_account",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("default_targets", sa.ARRAY(sa.String()), server_default="{}", nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("last_push_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by_user_id"], ["accounts_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integrations_connection_workspace",
        "integrations_connection",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_connection_workspace_provider",
        "integrations_connection",
        ["workspace_id", "provider"],
        unique=False,
    )
    op.create_table(
        "integrations_push_log",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("connection_id", sa.UUID(), nullable=False),
        sa.Column("signal_id", sa.Text(), nullable=True),
        sa.Column("pipeline_item_id", sa.Text(), nullable=True),
        sa.Column("target", sa.String(length=120), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "success",
                "failed",
                "dead_letter",
                name="integrations_push_status",
                native_enum=False,
                length=24,
            ),
            nullable=False,
        ),
        sa.Column(
            "request",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column(
            "error_code",
            sa.Enum(
                "auth",
                "permission",
                "rate_limited",
                "validation",
                "not_found",
                "transient",
                "unknown",
                name="integrations_push_error_code",
                native_enum=False,
                length=24,
            ),
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("provider_response_id", sa.String(length=255), nullable=True),
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
            ["connection_id"], ["integrations_connection.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integrations_push_log_connection",
        "integrations_push_log",
        ["connection_id"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_push_log_idempotency",
        "integrations_push_log",
        ["connection_id", "idempotency_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_integrations_push_log_idempotency_key"),
        "integrations_push_log",
        ["idempotency_key"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_push_log_retry",
        "integrations_push_log",
        ["status", "retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_integrations_push_log_status", "integrations_push_log", ["status"], unique=False
    )
    op.create_index(
        "ix_integrations_push_log_workspace",
        "integrations_push_log",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_integrations_push_log_workspace", table_name="integrations_push_log")
    op.drop_index("ix_integrations_push_log_status", table_name="integrations_push_log")
    op.drop_index("ix_integrations_push_log_retry", table_name="integrations_push_log")
    op.drop_index(
        op.f("ix_integrations_push_log_idempotency_key"), table_name="integrations_push_log"
    )
    op.drop_index("ix_integrations_push_log_idempotency", table_name="integrations_push_log")
    op.drop_index("ix_integrations_push_log_connection", table_name="integrations_push_log")
    op.drop_table("integrations_push_log")
    op.drop_index(
        "ix_integrations_connection_workspace_provider", table_name="integrations_connection"
    )
    op.drop_index("ix_integrations_connection_workspace", table_name="integrations_connection")
    op.drop_table("integrations_connection")
