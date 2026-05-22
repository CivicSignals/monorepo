"""merge migration heads (convergence churn)

Revision ID: b0b55b4309dd
Revises: 42a17cb82fe1, d5497133d4c0
Create Date: 2026-05-22 12:52:31.661468
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "b0b55b4309dd"
down_revision: str | None = ("42a17cb82fe1", "d5497133d4c0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
