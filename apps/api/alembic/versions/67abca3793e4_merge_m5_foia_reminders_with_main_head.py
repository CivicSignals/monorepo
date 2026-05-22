"""merge m5 foia reminders with main head

Revision ID: 67abca3793e4
Revises: 1ec885dd9c7a, bb5285cd2571
Create Date: 2026-05-22 10:00:25.117699
"""
from __future__ import annotations

from collections.abc import Sequence

revision: str = '67abca3793e4'
down_revision: str | None = ('1ec885dd9c7a', 'bb5285cd2571')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
