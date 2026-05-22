"""merge E5 dedupe with main head

Revision ID: a4952051340a
Revises: 1fde8f102d7d, e0b66034a6e0
Create Date: 2026-05-22 11:04:38.348455
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "a4952051340a"
down_revision: str | None = ("1fde8f102d7d", "e0b66034a6e0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
