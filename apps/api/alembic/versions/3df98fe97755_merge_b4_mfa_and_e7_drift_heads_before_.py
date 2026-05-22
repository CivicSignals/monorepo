"""merge b4 mfa and e7 drift heads before f6

Revision ID: 3df98fe97755
Revises: 1fa8a33511ee, 69482144c038
Create Date: 2026-05-22 13:50:36.060414
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "3df98fe97755"
down_revision: str | None = ("1fa8a33511ee", "69482144c038")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
