"""merge e10 fuzzy dedupe with main heads

Revision ID: 94158ed77ba1
Revises: 5deb42185a35, a1e9b3c8d7f2, e10a1b2c3d4e
Create Date: 2026-05-22 12:43:36.926078
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "94158ed77ba1"
down_revision: str | None = ("5deb42185a35", "a1e9b3c8d7f2", "e10a1b2c3d4e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
