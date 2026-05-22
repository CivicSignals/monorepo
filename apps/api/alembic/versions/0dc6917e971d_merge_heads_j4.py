"""merge_heads_j4

Revision ID: 0dc6917e971d
Revises: 3b3e28c9d8e7, 94158ed77ba1, f0d67bb3d837
Create Date: 2026-05-22 13:18:41.225734
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0dc6917e971d"
down_revision: str | None = ("3b3e28c9d8e7", "94158ed77ba1", "f0d67bb3d837")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
