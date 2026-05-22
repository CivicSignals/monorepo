"""merge E1 extraction tables with main heads

A no-op merge revision unifying the E1 head (``7200d4cf8ba7`` — the extraction
job/candidate tables + the ingestion discovery index) with the concurrent main
head (``74ac0372c98f``). Both descend from independent table sets, so the merge
carries no DDL; it only restores a single linear head (the MIGRATION RULE).

Revision ID: 3427d3582c8b
Revises: 7200d4cf8ba7, 74ac0372c98f
Create Date: 2026-05-22 09:23:04.381583
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "3427d3582c8b"
down_revision: str | None = ("7200d4cf8ba7", "74ac0372c98f")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
