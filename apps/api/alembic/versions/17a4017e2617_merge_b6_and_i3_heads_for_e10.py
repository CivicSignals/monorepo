"""Merge B6 invitations and I3 FTS heads to unblock E10 fuzzy-dedupe migration.

Revision ID: 17a4017e2617
Revises: 04b3d426c567, 92530196f0fe
Create Date: 2026-05-22 11:54:03.874218
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "17a4017e2617"
down_revision: str | Sequence[str] | None = ("04b3d426c567", "92530196f0fe")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
