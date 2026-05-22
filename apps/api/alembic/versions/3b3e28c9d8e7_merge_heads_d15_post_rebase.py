"""merge heads d15 post-rebase

Revision ID: 3b3e28c9d8e7
Revises: 42a17cb82fe1, 8897650f86b7, d5497133d4c0
Create Date: 2026-05-22 12:56:15.123856
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "3b3e28c9d8e7"
down_revision: str | None = ("42a17cb82fe1", "8897650f86b7", "d5497133d4c0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
