"""merge_heads_pre_b2_merge

Revision ID: 42a17cb82fe1
Revises: 5deb42185a35, a1e9b3c8d7f2, b2a3c4d5e6f7
Create Date: 2026-05-22 12:37:37.835654
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = '42a17cb82fe1'
down_revision: str | None = ('5deb42185a35', 'a1e9b3c8d7f2', 'b2a3c4d5e6f7')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
