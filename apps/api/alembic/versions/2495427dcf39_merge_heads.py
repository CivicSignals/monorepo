"""merge heads

Revision ID: 2495427dcf39
Revises: 04b3d426c567, 92530196f0fe
Create Date: 2026-05-22 11:58:15.122200
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "2495427dcf39"
down_revision: str | None = ("04b3d426c567", "92530196f0fe")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
