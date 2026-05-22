"""merge J1 pipeline with PR57 alembic head

Revision ID: 68b590c59c40
Revises: 527f1051b529, b532c18ae083
Create Date: 2026-05-22 08:57:55.186136
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "68b590c59c40"
down_revision: str | None = ("527f1051b529", "b532c18ae083")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
