"""m5_foia_reminder_columns

Revision ID: a1b2c3d4e5f6
Revises: 3427d3582c8b
Create Date: 2026-05-22 10:00:00.000000

Adds reminder-config columns to ``foia_request`` (M5 — FOIA reminder rules).

New columns:
- ``reminder_enabled``       (boolean, NOT NULL, default true)
- ``reminder_days``          (integer, NOT NULL, default 20)
- ``reminder_interval_days`` (integer, NOT NULL, default 7)
- ``reminder_max``           (integer, NOT NULL, default 3)
- ``last_reminded_at``       (timestamptz, nullable)
- ``reminder_count``         (integer, NOT NULL, default 0)

Also adds a partial index ``ix_foia_request_reminder_scan`` on
``(status, reminder_enabled)`` filtered to ``status = 'sent' AND
reminder_enabled = true`` to accelerate the beat-task's overdue-scan.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1ec885dd9c7a"
down_revision: str | None = "3427d3582c8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Defaults mirror the model constants in foia/models.py.
_DEFAULT_REMINDER_DAYS = 20
_DEFAULT_REMINDER_INTERVAL_DAYS = 7
_DEFAULT_REMINDER_MAX = 3


def upgrade() -> None:
    op.add_column(
        "foia_request",
        sa.Column(
            "reminder_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "foia_request",
        sa.Column(
            "reminder_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text(str(_DEFAULT_REMINDER_DAYS)),
        ),
    )
    op.add_column(
        "foia_request",
        sa.Column(
            "reminder_interval_days",
            sa.Integer(),
            nullable=False,
            server_default=sa.text(str(_DEFAULT_REMINDER_INTERVAL_DAYS)),
        ),
    )
    op.add_column(
        "foia_request",
        sa.Column(
            "reminder_max",
            sa.Integer(),
            nullable=False,
            server_default=sa.text(str(_DEFAULT_REMINDER_MAX)),
        ),
    )
    op.add_column(
        "foia_request",
        sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "foia_request",
        sa.Column(
            "reminder_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_index(
        "ix_foia_request_reminder_scan",
        "foia_request",
        ["status", "reminder_enabled"],
        unique=False,
        postgresql_where=sa.text("status = 'sent' AND reminder_enabled = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_foia_request_reminder_scan", table_name="foia_request")
    op.drop_column("foia_request", "reminder_count")
    op.drop_column("foia_request", "last_reminded_at")
    op.drop_column("foia_request", "reminder_max")
    op.drop_column("foia_request", "reminder_interval_days")
    op.drop_column("foia_request", "reminder_days")
    op.drop_column("foia_request", "reminder_enabled")
