"""merge E4 signals with N3/J3 head

Revision ID: 0a3f28b23f5d
Revises: 2364e9d3851b, 718a00f802f8
Create Date: 2026-05-22 10:28:00.988307
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0a3f28b23f5d"
down_revision: str | None = ("2364e9d3851b", "718a00f802f8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
