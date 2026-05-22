"""merge m5 foia reminders with main heads

Revision ID: abf7d4881fa1
Revises: 1ec885dd9c7a, 0a3f28b23f5d, 0a409a08021a
Create Date: 2026-05-22 10:37:19.805695
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "abf7d4881fa1"
down_revision: str | None = ("1ec885dd9c7a", "0a3f28b23f5d", "0a409a08021a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
