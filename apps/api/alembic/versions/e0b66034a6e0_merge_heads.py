"""merge heads

Revision ID: e0b66034a6e0
Revises: abf7d4881fa1, cf8e83e2f234
Create Date: 2026-05-22 10:52:11.034365
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "e0b66034a6e0"
down_revision: str | None = ("abf7d4881fa1", "cf8e83e2f234")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
