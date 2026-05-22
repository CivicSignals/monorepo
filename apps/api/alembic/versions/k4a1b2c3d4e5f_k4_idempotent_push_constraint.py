"""k4 idempotent push unique constraint on successful pushes

Adds a partial unique index on ``integrations_push_log(connection_id,
idempotency_key)`` WHERE ``status = 'success' AND idempotency_key IS NOT
NULL``.  This is the race guard for the K4 idempotent-push feature: only one
successful push per (connection, idempotency_key) pair can be committed;
a concurrent second create attempt raises ``IntegrityError`` and is retried
as an update against the winner's external_id.

Revision ID: k4a1b2c3d4e5f
Revises: 96be42079e00
Create Date: 2026-05-22 13:41:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "k4a1b2c3d4e5f"
down_revision: str | None = "96be42079e00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Partial unique index: only one successful push per (connection, key).
    # The WHERE clause makes it a PostgreSQL partial index so pending/failed/
    # dead_letter rows with the same key are unaffected (multiple retry
    # attempts for the same key are still legal).
    op.create_index(
        "uq_integrations_push_log_idempotency_success",
        "integrations_push_log",
        ["connection_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status = 'success' AND idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_integrations_push_log_idempotency_success",
        table_name="integrations_push_log",
    )
