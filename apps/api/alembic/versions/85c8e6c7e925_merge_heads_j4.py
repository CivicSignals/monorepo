"""merge_heads_j4

Revision ID: 85c8e6c7e925
Revises: 42a17cb82fe1, 94158ed77ba1, d5497133d4c0
Create Date: 2026-05-22 13:04:03.828976
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "85c8e6c7e925"
down_revision: str | None = ("42a17cb82fe1", "94158ed77ba1", "d5497133d4c0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
