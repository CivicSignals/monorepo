"""merge F3 workspace score with main heads

Revision ID: 61ecc440ad34
Revises: 42a17cb82fe1, 94158ed77ba1, d5497133d4c0, e6250f1bfd6b
Create Date: 2026-05-22 13:03:20.255810
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "61ecc440ad34"
down_revision: str | None = ("42a17cb82fe1", "94158ed77ba1", "d5497133d4c0", "e6250f1bfd6b")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
