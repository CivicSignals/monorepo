"""merge l3 webhook with k2 e10 d15 main heads

Revision ID: 7bc00b0b6c8d
Revises: 3b3e28c9d8e7, 94158ed77ba1, b76e90943db2, f0d67bb3d837
Create Date: 2026-05-22 13:13:32.664103
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "7bc00b0b6c8d"
down_revision: str | None = ("3b3e28c9d8e7", "94158ed77ba1", "b76e90943db2", "f0d67bb3d837")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
