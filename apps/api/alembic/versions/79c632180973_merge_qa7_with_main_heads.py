"""merge qa7 with main heads

Revision ID: 79c632180973
Revises: 3849757e8240, qa7_a1b2c3d4e5f6
Create Date: 2026-05-22 14:37:34.740686
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "79c632180973"
down_revision: str | None = ("3849757e8240", "qa7_a1b2c3d4e5f6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
