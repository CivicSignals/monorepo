"""merge F3 workspace score with main heads

Revision ID: 6a8ae317b703
Revises: 3b3e28c9d8e7, 94158ed77ba1, e6250f1bfd6b, f0d67bb3d837
Create Date: 2026-05-22 13:10:24.210713
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "6a8ae317b703"
down_revision: str | None = ("3b3e28c9d8e7", "94158ed77ba1", "e6250f1bfd6b", "f0d67bb3d837")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
