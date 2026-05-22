"""merge B8 api tokens with latest main head 2

Revision ID: 333218c5e1ba
Revises: 0a409a08021a, 9b6ba082a256
Create Date: 2026-05-22 10:34:27.495865
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "333218c5e1ba"
down_revision: str | None = ("0a409a08021a", "9b6ba082a256")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
