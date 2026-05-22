"""merge auth and ingestion/extraction migration heads

Revision ID: aa34b8a78218
Revises: 1052bf5815da, 91d6709f6982
Create Date: 2026-05-22 06:12:27.856865
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "aa34b8a78218"
down_revision: str | None = ("1052bf5815da", "91d6709f6982")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
