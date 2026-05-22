"""merge I3 fts index with main head

Revision ID: 92530196f0fe
Revises: d4e5f6a7b8c9, e5fd99021455
Create Date: 2026-05-22 11:43:33.165226
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "92530196f0fe"
down_revision: str | None = ("d4e5f6a7b8c9", "e5fd99021455")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
