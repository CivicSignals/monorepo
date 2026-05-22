"""merge B8 api tokens and B9 audit/E1 heads

Revision ID: 554c8e28e2ba
Revises: 129c1e8af6d9, bb5285cd2571
Create Date: 2026-05-22 09:50:43.035146
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "554c8e28e2ba"
down_revision: str | None = ("129c1e8af6d9", "bb5285cd2571")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
