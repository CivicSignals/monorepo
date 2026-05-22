"""merge b9 audit and e1 extraction

Revision ID: bb5285cd2571
Revises: 3427d3582c8b, c9f1a2b3d4e5
Create Date: 2026-05-22 09:42:49.551572
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "bb5285cd2571"
down_revision: str | None = ("3427d3582c8b", "c9f1a2b3d4e5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
