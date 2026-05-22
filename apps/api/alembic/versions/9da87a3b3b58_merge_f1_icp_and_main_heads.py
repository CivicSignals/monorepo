"""merge F1 icp and main heads

Revision ID: 9da87a3b3b58
Revises: bb5285cd2571, f34304357c27
Create Date: 2026-05-22 09:54:49.079571
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "9da87a3b3b58"
down_revision: str | None = ("bb5285cd2571", "f34304357c27")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
