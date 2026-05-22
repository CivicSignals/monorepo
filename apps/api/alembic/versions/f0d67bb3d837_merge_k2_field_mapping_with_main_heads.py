"""merge k2 field mapping with main heads

Revision ID: f0d67bb3d837
Revises: 42a17cb82fe1, 83b52f2c376b, d5497133d4c0
Create Date: 2026-05-22 12:54:16.120736
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "f0d67bb3d837"
down_revision: str | tuple[str, ...] | None = (
    "42a17cb82fe1",
    "83b52f2c376b",
    "d5497133d4c0",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
