"""merge b6 invitations with main heads

Revision ID: 04b3d426c567
Revises: 479ad775f192, 68e4c0a07d07
Create Date: 2026-05-22 11:39:47.791790
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "04b3d426c567"
down_revision: str | None = ("479ad775f192", "68e4c0a07d07")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
