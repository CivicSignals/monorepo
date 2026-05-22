"""merge B8 api tokens with latest main head

Revision ID: 9b6ba082a256
Revises: 718a00f802f8, 7e624082bc5f
Create Date: 2026-05-22 10:27:54.594739
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "9b6ba082a256"
down_revision: str | None = ("718a00f802f8", "7e624082bc5f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
