"""merge E5 dedupe with C6 contacts head

Revision ID: 1fde8f102d7d
Revises: 0a409a08021a, 2effacb67656
Create Date: 2026-05-22 10:54:58.528167
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "1fde8f102d7d"
down_revision: str | None = ("0a409a08021a", "2effacb67656")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
