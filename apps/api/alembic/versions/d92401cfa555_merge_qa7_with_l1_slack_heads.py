"""merge qa7 with L1 slack heads

Revision ID: d92401cfa555
Revises: 79c632180973, a7413b961483
Create Date: 2026-05-22 15:13:20.145777
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "d92401cfa555"
down_revision: str | None = ("79c632180973", "a7413b961483")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
