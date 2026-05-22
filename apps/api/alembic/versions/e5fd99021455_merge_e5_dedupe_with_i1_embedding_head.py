"""merge E5 dedupe with I1 embedding head

Revision ID: e5fd99021455
Revises: 479ad775f192, a4952051340a
Create Date: 2026-05-22 11:18:31.614005
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "e5fd99021455"
down_revision: str | None = ("479ad775f192", "a4952051340a")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
