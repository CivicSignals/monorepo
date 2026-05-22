"""merge D4 schedule with main heads (rebase)

Revision ID: f61a776e6eab
Revises: 2495427dcf39, ab9f76ebd73e
Create Date: 2026-05-22 12:01:21.568960
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f61a776e6eab"
down_revision: str | None = ("2495427dcf39", "ab9f76ebd73e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
