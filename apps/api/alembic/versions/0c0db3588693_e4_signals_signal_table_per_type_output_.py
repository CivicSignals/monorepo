"""E4 signals_signal table + per-type output schema

Creates the canonical global ``signals_signal`` table (doc 07 §2 "signals",
doc 14 §4.2) that the extraction funnel (E1) promotes validated typed candidates
into. Enables the ``vector`` (pgvector) extension so the ``vector_embedding``
column (doc 07 ``VECTOR(1536)``) can exist; the column is populated by I1, and the
ANN (ivfflat) index is added by I1 once there is data to tune it against.

Indexes target the doc 07 access patterns: the unique dedupe key
``(entity_id, signal_type, content_hash)`` (``signals_dedupe_idx``), the ICP/match
pre-filter ``(entity_id, signal_type)`` (``signals_entity_type_idx``), and feed
recency ``(observed_at)`` (``signals_observed_at_idx``).

Revision ID: 0c0db3588693
Revises: 3427d3582c8b
Create Date: 2026-05-22 09:39:20.622906
"""

from __future__ import annotations

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0c0db3588693"
down_revision: str | None = "3427d3582c8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector backs the ``vector_embedding`` column (doc 07 ``VECTOR(1536)``),
    # populated by I1 (embeddings). Idempotent so it co-exists with other migrations
    # that may also enable it.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "signals_signal",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("entity_name_raw", sa.String(length=500), nullable=True),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("recipe_id", sa.String(length=255), nullable=False),
        sa.Column("extraction_job_id", sa.UUID(), nullable=True),
        sa.Column("source_candidate_id", sa.UUID(), nullable=True),
        sa.Column(
            "raw_document_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'new'"), nullable=False),
        sa.Column("is_degraded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("review_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("vector_embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities_entity.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # NULLS NOT DISTINCT (PG15+) so resolution-pending signals (entity_id NULL,
    # doc 19 §4.3) still dedupe — Postgres treats NULLs as distinct by default.
    op.create_index(
        "signals_dedupe_idx",
        "signals_signal",
        ["entity_id", "signal_type", "content_hash"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "signals_entity_type_idx", "signals_signal", ["entity_id", "signal_type"], unique=False
    )
    op.create_index("signals_observed_at_idx", "signals_signal", ["observed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("signals_observed_at_idx", table_name="signals_signal")
    op.drop_index("signals_entity_type_idx", table_name="signals_signal")
    op.drop_index("signals_dedupe_idx", table_name="signals_signal")
    op.drop_table("signals_signal")
    # The ``vector`` extension is left enabled — other tables (I1) depend on it and
    # dropping a shared extension on this table's downgrade would be incorrect.
