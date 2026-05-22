"""merge D4 schedule with K1 main head

Revision ID: b282915c8b8f
Revises: e908c20ae8c7, f61a776e6eab
Create Date: 2026-05-22 12:09:56.712994
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "b282915c8b8f"
down_revision: str | None = ("e908c20ae8c7", "f61a776e6eab")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
