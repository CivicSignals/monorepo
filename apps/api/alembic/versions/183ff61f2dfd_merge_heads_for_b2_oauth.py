"""merge_heads_for_b2_oauth

Revision ID: 183ff61f2dfd
Revises: 04b3d426c567, 92530196f0fe
Create Date: 2026-05-22 11:56:19.684963
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "183ff61f2dfd"
down_revision: str | None = ("04b3d426c567", "92530196f0fe")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
