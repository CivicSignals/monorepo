"""merge k4 idempotent push with main heads

Revision ID: a459691c1a05
Revises: d7c6c4b3e3d1, k4a1b2c3d4e5f
Create Date: 2026-05-22 13:58:38.876268
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "a459691c1a05"
down_revision: str | tuple[str, ...] | None = ("d7c6c4b3e3d1", "k4a1b2c3d4e5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
