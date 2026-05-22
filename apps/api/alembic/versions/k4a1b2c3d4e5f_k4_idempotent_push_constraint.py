"""k4 idempotent push — canonical external-id registry table

Adds the ``integrations_push_idempotency`` table: one row per
``(connection_id, idempotency_key)`` holding the canonical ``external_id``
returned by the provider on a successful push.  This table is upserted on
each successful push (PostgreSQL ``INSERT … ON CONFLICT DO UPDATE``) so:

- Sequential re-pushes look up the existing ``external_id`` and route through
  the provider's update path (no duplicate CRM objects).
- Concurrent duplicate pushes race on the composite primary key: the winner's
  INSERT succeeds; the loser's INSERT hits the conflict and updates the row
  with the same ``external_id`` — harmless.

``integrations_push_log`` remains a pure append-only audit trail.

Revision ID: k4a1b2c3d4e5f
Revises: 96be42079e00
Create Date: 2026-05-22 13:41:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "k4a1b2c3d4e5f"
down_revision: str | None = "96be42079e00"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integrations_push_idempotency",
        sa.Column(
            "connection_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("integrations_connection.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(255),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "external_id",
            sa.String(255),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("integrations_push_idempotency")
