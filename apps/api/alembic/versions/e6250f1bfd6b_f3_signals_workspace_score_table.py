"""F3 signals_workspace_score table

Creates the sparse per-workspace signal score table (F3, doc 14 §5) — the crux of
the scaling story: a ``signals_signal`` row is **global** (doc 07 §3), so workspace
relevance is captured here, and a row exists **only** when a signal scored at/above
the workspace's ICP threshold (doc 14 §5.2, §6.2). This turns the global signal
corpus into a per-workspace ranked feed (G1).

Indexes target the hot feed query (doc 14 §5.3): the composite
``(workspace_id, status, score DESC, created_at DESC)`` is a single index range scan
(``ix_sws_workspace_status_score``), and ``ix_sws_signal`` backs the matcher fan-out
/ F6 backfill upsert lookup + workspace-deletion cleanup (doc 14 §10.5). Unique
``(workspace_id, signal_id)`` (doc 14 §5.3) so a signal earns at most one score row
per workspace and the upsert (ON CONFLICT) is idempotent (doc 14 §7.3).

Touches only the ``signals_*`` namespace (this module owns it, doc 06 §3, §4);
nothing else is migrated here. Monthly partitioning (doc 14 §5.4) is deferred — the
MVP keeps a plain table; the composite index is identical either way, so the feed
query is forward-compatible.

Revision ID: e6250f1bfd6b
Revises: e5fd99021455
Create Date: 2026-05-22 11:56:49.690601
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e6250f1bfd6b"
down_revision: str | None = "e5fd99021455"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signals_workspace_score",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("signal_id", sa.UUID(), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column(
            "score_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'new'"), nullable=False),
        sa.Column(
            "matched_via", sa.String(length=64), server_default=sa.text("'icp'"), nullable=False
        ),
        sa.Column(
            "matched_signal_type",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("matched_country", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("matched_state", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "matched_entity_kind",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "matched_size_band",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "matched_keywords",
            postgresql.ARRAY(sa.String()),
            server_default=sa.text("'{}'::text[]"),
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
        sa.CheckConstraint(
            "status IN ('new', 'reviewed', 'pinned', 'pushed', 'dismissed')",
            name="signals_workspace_score_status_check",
        ),
        sa.CheckConstraint(
            "score >= 0 AND score <= 100", name="signals_workspace_score_range_check"
        ),
        sa.ForeignKeyConstraint(["signal_id"], ["signals_signal.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "signal_id", name="uq_sws_workspace_signal"),
    )
    op.create_index("ix_sws_signal", "signals_workspace_score", ["signal_id"], unique=False)
    # The hot feed query (doc 14 §5.3): narrow by workspace + status, order by score
    # then recency — a single index range scan.
    op.create_index(
        "ix_sws_workspace_status_score",
        "signals_workspace_score",
        [
            "workspace_id",
            "status",
            sa.literal_column("score DESC"),
            sa.literal_column("created_at DESC"),
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sws_workspace_status_score", table_name="signals_workspace_score")
    op.drop_index("ix_sws_signal", table_name="signals_workspace_score")
    op.drop_table("signals_workspace_score")
