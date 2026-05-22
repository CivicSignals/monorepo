"""merge e9 ocr heads before e9 migration

Revision ID: 3753b6a9ea3f
Revises: 04b3d426c567, 92530196f0fe
Create Date: 2026-05-22 12:02:22.849346
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = '3753b6a9ea3f'
down_revision: str | None = ('04b3d426c567', '92530196f0fe')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
