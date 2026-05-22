"""merge l3 with b4 mfa and latest main

Revision ID: d7c6c4b3e3d1
Revises: 1fa8a33511ee, 69482144c038, 7bc00b0b6c8d
Create Date: 2026-05-22 13:35:14.458658
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = 'd7c6c4b3e3d1'
down_revision: str | None = ('1fa8a33511ee', '69482144c038', '7bc00b0b6c8d')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
