"""merge digest + signals-workflow heads

Revision ID: 26d933c04a76
Revises: 3685697d1afa, f5a1b2c3d4e5
Create Date: 2026-05-23 00:33:31.771384
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "26d933c04a76"
down_revision: str | None = ("3685697d1afa", "f5a1b2c3d4e5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
