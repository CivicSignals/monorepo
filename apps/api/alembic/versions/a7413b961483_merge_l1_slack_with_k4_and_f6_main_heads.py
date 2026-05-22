"""merge_l1_slack_with_k4_and_f6_main_heads

Merge three concurrent heads: L1 Slack channel table (b1a2c3d4e5f6),
K4 idempotent push merge (a459691c1a05), and pre-F6 merge (3849757e8240)
into a single alembic head.

Revision ID: a7413b961483
Revises: 3849757e8240, a459691c1a05, b1a2c3d4e5f6
Create Date: 2026-05-22 14:38:52.291014
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "a7413b961483"
down_revision: str | None = ("3849757e8240", "a459691c1a05", "b1a2c3d4e5f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass  # merge-only; no schema changes


def downgrade() -> None:
    pass  # merge-only; no schema changes
