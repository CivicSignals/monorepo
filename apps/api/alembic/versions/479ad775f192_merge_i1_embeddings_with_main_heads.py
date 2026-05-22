"""merge I1 embeddings with main heads

Unifies the I1 ivfflat-index head (``12ca7f355ee0``) with the latest main merge
head (``e0b66034a6e0``) after rebase, so the tree has a single head. No DDL — a
pure mergepoint.

Revision ID: 479ad775f192
Revises: 12ca7f355ee0, e0b66034a6e0
Create Date: 2026-05-22 11:04:15.261717
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "479ad775f192"
down_revision: str | None = ("12ca7f355ee0", "e0b66034a6e0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
