"""merge_d3_raw_storage_and_n1_billing_heads

Revision ID: 4cd02678ecc1
Revises: 6159b96ba523, a9f3e2b1c4d5
Create Date: 2026-05-22 08:38:55.503554
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '4cd02678ecc1'
down_revision: str | None = ('6159b96ba523', 'a9f3e2b1c4d5')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
