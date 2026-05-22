"""merge migration heads (E9 OCR + concurrent)

Revision ID: d5497133d4c0
Revises: 5deb42185a35, a1e9b3c8d7f2
Create Date: 2026-05-22 12:40:22.989945
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "d5497133d4c0"
down_revision: str | None = ("5deb42185a35", "a1e9b3c8d7f2")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
