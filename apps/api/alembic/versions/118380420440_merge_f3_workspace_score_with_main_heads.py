"""merge F3 workspace score with main heads

Revision ID: 118380420440
Revises: 5deb42185a35, e6250f1bfd6b
Create Date: 2026-05-22 12:33:58.389860
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "118380420440"
down_revision: str | None = ("5deb42185a35", "e6250f1bfd6b")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
