"""merge l3 webhook with main heads

Revision ID: b76e90943db2
Revises: 5deb42185a35, a1e9b3c8d7f2, c3a1d8f7e920
Create Date: 2026-05-22 12:40:40.227505
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "b76e90943db2"
down_revision: str | None = ("5deb42185a35", "a1e9b3c8d7f2", "c3a1d8f7e920")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
