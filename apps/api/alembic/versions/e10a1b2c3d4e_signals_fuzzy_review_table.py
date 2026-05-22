"""E10: signals_fuzzy_review table + merged_into column on signals_signal.

Adds the ``signals_fuzzy_review`` table (doc 19 §7.4; E10). Each row represents a
fuzzy-match candidate (high-stakes signal types: rfp_posted / contract_expiring)
that was routed to human review instead of auto-merging, during the first
``GRADUATION_COUNT`` (100) fuzzy matches per signal type. A reviewer approves
(triggering a merge) or rejects (keeping the candidate as a distinct signal).

Also adds ``merged_into`` (UUID, nullable) to ``signals_signal`` — a soft-delete
pointer set when a candidate is merged into a surviving signal (approve / auto-merge).
Merged rows keep their raw_document_ids for the audit trail but are excluded from
list_signals / feed queries (filtered on ``status != 'merged'``).

Revision ID: e10a1b2c3d4e
Revises: 17a4017e2617
Create Date: 2026-05-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e10a1b2c3d4e"
down_revision: str | Sequence[str] | None = "17a4017e2617"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signals_fuzzy_review",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("candidate_signal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("matched_signal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "signals_fuzzy_review_type_status_idx",
        "signals_fuzzy_review",
        ["signal_type", "status"],
    )
    op.create_index(
        "signals_fuzzy_review_candidate_idx",
        "signals_fuzzy_review",
        ["candidate_signal_id"],
    )
    op.create_index(
        "signals_fuzzy_review_matched_idx",
        "signals_fuzzy_review",
        ["matched_signal_id"],
    )

    # Soft-delete support for merged candidates (E10 recovery fix).
    # merged_into: loose UUID ref to the surviving signal after fuzzy-dedupe merge.
    op.add_column(
        "signals_signal",
        sa.Column("merged_into", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("signals_signal", "merged_into")
    op.drop_index("signals_fuzzy_review_matched_idx", table_name="signals_fuzzy_review")
    op.drop_index("signals_fuzzy_review_candidate_idx", table_name="signals_fuzzy_review")
    op.drop_index("signals_fuzzy_review_type_status_idx", table_name="signals_fuzzy_review")
    op.drop_table("signals_fuzzy_review")
