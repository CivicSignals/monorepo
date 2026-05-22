"""merge B8 api tokens with N3 metering/F1 ICP/J3 pipeline heads

Revision ID: 7e624082bc5f
Revises: 554c8e28e2ba, 631d5f3f9d1c, e1f2a3b4c5d6
Create Date: 2026-05-22 10:19:05.858653
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "7e624082bc5f"
down_revision: str | None = ("554c8e28e2ba", "631d5f3f9d1c", "e1f2a3b4c5d6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
