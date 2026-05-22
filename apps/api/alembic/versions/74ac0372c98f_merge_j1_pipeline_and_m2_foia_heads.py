"""merge J1 pipeline and M2 foia heads

Revision ID: 74ac0372c98f
Revises: 5d3748bed3c5, 68b590c59c40
Create Date: 2026-05-22 09:16:04.295045
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "74ac0372c98f"
down_revision: str | None = ("5d3748bed3c5", "68b590c59c40")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
