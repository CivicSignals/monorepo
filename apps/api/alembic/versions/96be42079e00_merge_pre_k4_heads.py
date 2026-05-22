"""merge pre-k4 heads

Revision ID: 96be42079e00
Revises: 1fa8a33511ee, 69482144c038
Create Date: 2026-05-22 13:40:14.826268
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "96be42079e00"
down_revision: str | tuple[str, ...] | None = ("1fa8a33511ee", "69482144c038")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
