"""merge pre-f6 heads

Revision ID: 3849757e8240
Revises: 3df98fe97755, d7c6c4b3e3d1
Create Date: 2026-05-22 14:18:14.559416
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "3849757e8240"
down_revision: str | None = ("3df98fe97755", "d7c6c4b3e3d1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
