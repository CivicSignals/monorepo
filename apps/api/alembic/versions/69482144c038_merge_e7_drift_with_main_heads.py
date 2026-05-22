"""merge e7 drift with main heads

Revision ID: 69482144c038
Revises: 0dc6917e971d, 6a8ae317b703, 785be61c50d1, 85c8e6c7e925
Create Date: 2026-05-22 13:22:32.538774
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "69482144c038"
down_revision: str | None = ("0dc6917e971d", "6a8ae317b703", "785be61c50d1", "85c8e6c7e925")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
