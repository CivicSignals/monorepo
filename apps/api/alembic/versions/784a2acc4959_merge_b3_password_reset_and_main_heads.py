"""merge b3 password reset and main heads

Revision ID: 784a2acc4959
Revises: a1b2c3d4e5f6, b3c1d2e4f5a6
Create Date: 2026-05-22 07:33:52.867747
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "784a2acc4959"
down_revision: str | None = ("a1b2c3d4e5f6", "b3c1d2e4f5a6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
