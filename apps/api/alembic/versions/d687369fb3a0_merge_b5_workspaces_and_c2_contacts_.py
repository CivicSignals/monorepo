"""merge b5 workspaces and c2 contacts heads

Revision ID: d687369fb3a0
Revises: 68fd32317974, a1b2c3d4e5f6
Create Date: 2026-05-22 07:33:53.498662
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "d687369fb3a0"
down_revision: str | None = ("68fd32317974", "a1b2c3d4e5f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
