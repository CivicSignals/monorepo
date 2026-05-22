"""F5 signals_signal_feedback table

Creates the per-(workspace, signal) user feedback table (F5, doc 14 §12 "negative
training"): a user marks a scored signal ``relevant`` / ``not_relevant`` /
``wrong_extraction``. The first two aggregate (per signal type) into a bounded
per-workspace nudge to the signal-type weight that re-weights *subsequent* scores;
``wrong_extraction`` is an extraction-quality flag that does not alter scoring (it is
recorded + surfaced for the QA-7 / E-epic extraction-quality review).

The natural key is ``(workspace_id, signal_id, user_id)`` — at most one live verdict
per user per (workspace, signal); changing a verdict is an upsert on this constraint,
retracting is a delete. Indexes target the aggregate the scorer reads (workspace) and
the detail/feed "this user's verdict" + signal-deletion cleanup (workspace + signal).

Touches only the ``signals_*`` namespace (this module owns it, doc 06 §3, §4); bases
on the current single head so the graph stays single-headed.

Revision ID: f5a1b2c3d4e5
Revises: d92401cfa555
Create Date: 2026-05-22 16:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f5a1b2c3d4e5"
down_revision: str | None = "d92401cfa555"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signals_signal_feedback",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("signal_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
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
        sa.CheckConstraint(
            "kind IN ('relevant', 'not_relevant', 'wrong_extraction')",
            name="signals_signal_feedback_kind_check",
        ),
        sa.ForeignKeyConstraint(["signal_id"], ["signals_signal.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["accounts_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "signal_id", "user_id", name="uq_signal_feedback_ws_signal_user"
        ),
    )
    op.create_index(
        "ix_signal_feedback_workspace", "signals_signal_feedback", ["workspace_id"], unique=False
    )
    op.create_index(
        "ix_signal_feedback_workspace_signal",
        "signals_signal_feedback",
        ["workspace_id", "signal_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_signal_feedback_workspace_signal", table_name="signals_signal_feedback")
    op.drop_index("ix_signal_feedback_workspace", table_name="signals_signal_feedback")
    op.drop_table("signals_signal_feedback")
