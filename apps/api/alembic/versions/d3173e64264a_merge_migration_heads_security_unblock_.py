"""merge migration heads (security-unblock chore)

Revision ID: d3173e64264a
Revises: 3b3e28c9d8e7, 94158ed77ba1, f0d67bb3d837
Create Date: 2026-05-22 13:06:16.246551
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "d3173e64264a"
down_revision: str | None = ("3b3e28c9d8e7", "94158ed77ba1", "f0d67bb3d837")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
