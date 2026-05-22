"""merge heads for d15

Revision ID: 8897650f86b7
Revises: 5deb42185a35, a1e9b3c8d7f2
Create Date: 2026-05-22 12:46:27.484298
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = '8897650f86b7'
down_revision: str | None = ('5deb42185a35', 'a1e9b3c8d7f2')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
