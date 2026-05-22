"""merge I1 vector index with c6 contacts head

Unifies the two divergent heads so the tree has a single head again: the I1
``signals_signal.vector_embedding`` ivfflat index (``c1d2e3f4a5b6``) and the C6
contacts correction-table head (``0a409a08021a``). No DDL — a pure mergepoint.

Revision ID: 12ca7f355ee0
Revises: 0a409a08021a, c1d2e3f4a5b6
Create Date: 2026-05-22 10:50:36.282264
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "12ca7f355ee0"
down_revision: str | None = ("0a409a08021a", "c1d2e3f4a5b6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
