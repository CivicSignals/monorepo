"""merge E4 signals with F1 ICP / B9 audit heads

Revision ID: 2364e9d3851b
Revises: 0c0db3588693, 9da87a3b3b58
Create Date: 2026-05-22 10:13:51.029572
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "2364e9d3851b"
down_revision: str | None = ("0c0db3588693", "9da87a3b3b58")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
