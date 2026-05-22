"""merge_b4_mfa_with_main_heads

Revision ID: 1fa8a33511ee
Revises: 0dc6917e971d, 6a8ae317b703, 85c8e6c7e925, b4c1d2e3f4a5
Create Date: 2026-05-22 13:21:22.092264
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "1fa8a33511ee"
down_revision: str | None = ("0dc6917e971d", "6a8ae317b703", "85c8e6c7e925", "b4c1d2e3f4a5")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
