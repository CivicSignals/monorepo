"""merge field-mapping-template + main heads

Revision ID: 5649cbdb832c
Revises: 1423dd9eb65a, 26d933c04a76
Create Date: 2026-05-23 00:52:04.390662
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "5649cbdb832c"
down_revision: str | None = ("1423dd9eb65a", "26d933c04a76")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
