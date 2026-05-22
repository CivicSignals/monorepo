"""merge K1 integrations with main

Revision ID: e908c20ae8c7
Revises: 2495427dcf39, a298967820ff
Create Date: 2026-05-22 12:05:19.411249
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "e908c20ae8c7"
down_revision: str | tuple[str, ...] | None = ("2495427dcf39", "a298967820ff")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
