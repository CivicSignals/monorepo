"""merge b5 and b3 heads

Revision ID: 467a515ea5a6
Revises: 784a2acc4959, d687369fb3a0
Create Date: 2026-05-22 08:02:14.155232
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "467a515ea5a6"
down_revision: str | None = ("784a2acc4959", "d687369fb3a0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
