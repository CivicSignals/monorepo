"""merge n3 metering and f1 icp heads

Revision ID: 631d5f3f9d1c
Revises: 7258f4526c5c, 9da87a3b3b58
Create Date: 2026-05-22 10:06:46.941493
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "631d5f3f9d1c"
down_revision: str | None = ("7258f4526c5c", "9da87a3b3b58")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
