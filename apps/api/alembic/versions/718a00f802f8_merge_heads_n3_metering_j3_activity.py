"""merge heads (N3 metering + J3 activity)

Revision ID: 718a00f802f8
Revises: 631d5f3f9d1c, e1f2a3b4c5d6
Create Date: 2026-05-22 10:26:50.274761
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "718a00f802f8"
down_revision: str | None = ("631d5f3f9d1c", "e1f2a3b4c5d6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
