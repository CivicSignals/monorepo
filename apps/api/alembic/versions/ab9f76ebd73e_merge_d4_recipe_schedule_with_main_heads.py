"""merge D4 recipe schedule with main heads

Revision ID: ab9f76ebd73e
Revises: 04b3d426c567, 10c081fb93d4, 92530196f0fe
Create Date: 2026-05-22 11:49:31.560417
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "ab9f76ebd73e"
down_revision: str | None = ("04b3d426c567", "10c081fb93d4", "92530196f0fe")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
