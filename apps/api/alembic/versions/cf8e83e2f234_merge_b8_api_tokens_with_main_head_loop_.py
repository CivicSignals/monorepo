"""merge B8 api tokens with main head (loop 1)

Revision ID: cf8e83e2f234
Revises: 0a3f28b23f5d, 333218c5e1ba
Create Date: 2026-05-22 10:35:38.601918
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "cf8e83e2f234"
down_revision: str | None = ("0a3f28b23f5d", "333218c5e1ba")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
