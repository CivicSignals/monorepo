"""H3 notifications digest_subscription table

Revision ID: 3685697d1afa
Revises: 5606a521ecf4
Create Date: 2026-05-22 19:59:08.956429

Per-(saved-search, user) digest schedule (H3): frequency off/daily/weekly, local
send hour + weekday, recipient timezone, and the distributed-safe dedupe columns
(``last_sent_period`` / ``last_sent_at``). The FK to ``searches_saved_search`` is
``ON DELETE CASCADE`` so deleting a saved search removes its digest subscriptions
(the H1 ``delete_saved_search`` seam).

Hand-trimmed to ONLY the H3 table + its indexes; alembic autogenerate also
surfaced unrelated drift (the FTS/ivfflat partial indexes and ``signals_fuzzy_
review``, all created via raw SQL it cannot round-trip) which is intentionally
left untouched here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3685697d1afa"
down_revision: str | None = "5606a521ecf4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications_digest_subscription",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("saved_search_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "frequency",
            sa.String(length=16),
            server_default=sa.text("'off'"),
            nullable=False,
        ),
        sa.Column("send_hour", sa.Integer(), server_default=sa.text("8"), nullable=False),
        sa.Column("weekday", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default=sa.text("'UTC'"),
            nullable=False,
        ),
        sa.Column("last_sent_period", sa.String(length=32), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
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
            ["saved_search_id"], ["searches_saved_search.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["accounts_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "saved_search_id",
            "user_id",
            name="uq_notifications_digest_subscription_search_user",
        ),
    )
    # Partial index serving the dispatch sweep (active = non-off subscriptions).
    op.create_index(
        "ix_notifications_digest_subscription_active",
        "notifications_digest_subscription",
        ["frequency"],
        unique=False,
        postgresql_where=sa.text("frequency <> 'off'"),
    )
    op.create_index(
        "ix_notifications_digest_subscription_workspace_id",
        "notifications_digest_subscription",
        ["workspace_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notifications_digest_subscription_workspace_id",
        table_name="notifications_digest_subscription",
    )
    op.drop_index(
        "ix_notifications_digest_subscription_active",
        table_name="notifications_digest_subscription",
        postgresql_where=sa.text("frequency <> 'off'"),
    )
    op.drop_table("notifications_digest_subscription")
