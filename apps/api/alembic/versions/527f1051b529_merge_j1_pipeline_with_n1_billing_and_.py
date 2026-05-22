"""merge J1 pipeline with N1 billing and FOIA heads

Revision ID: 527f1051b529
Revises: 02e0276be741, 4cd02678ecc1, d7dd938017a7
Create Date: 2026-05-22 08:41:41.705785
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "527f1051b529"
down_revision: str | None = ("02e0276be741", "4cd02678ecc1", "d7dd938017a7")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
