"""merge divergent heads (N1 + raw-doc)

Revision ID: b532c18ae083
Revises: 02e0276be741, 4cd02678ecc1
Create Date: 2026-05-22 08:50:45.734602
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "b532c18ae083"
down_revision: str | None = ("02e0276be741", "4cd02678ecc1")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
