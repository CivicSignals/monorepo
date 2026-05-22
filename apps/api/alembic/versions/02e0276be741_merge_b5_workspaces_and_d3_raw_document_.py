"""merge B5 workspaces and D3 raw-document heads

Revision ID: 02e0276be741
Revises: 467a515ea5a6, 6159b96ba523
Create Date: 2026-05-22 08:36:41.160952
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "02e0276be741"
down_revision: str | None = ("467a515ea5a6", "6159b96ba523")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
