"""ingestion recipe schedule table (D4)

Owned by the ``ingestion`` module (doc 06 §3, §4) — touches only
``ingestion_recipe_schedule``. The per-recipe scheduling run-state the cadence
dispatcher (``ingestion.dispatch_due_recipes``, doc 18 §3, §6) reads each beat
tick to decide whether a recipe is due (``now >= next_run_at``) and writes back
after enqueuing a crawl (advancing ``last_run_at`` / ``next_run_at``). Keyed by
the recipe **slug string** (recipes are versioned YAML — there is no
``recipes_recipe`` table — and ingestion does not FK across the module boundary,
doc 06 §3; same convention as ``ingestion_raw_document.recipe_id``).

``UNIQUE (recipe_id)`` makes the dispatcher's get-or-create idempotent (one
run-state row per recipe). The ``id`` PK is generated application-side
(``models._new_uuid``), so this migration needs no server-side UUID function.
Chained after the current head to keep a single linear migration head.

Revision ID: 10c081fb93d4
Revises: 479ad775f192
Create Date: 2026-05-22 09:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "10c081fb93d4"
down_revision: str | None = "479ad775f192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_recipe_schedule",
        # PK is populated application-side (models._new_uuid), so no server-side
        # UUID function / pgcrypto extension is needed.
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recipe_id", sa.String(length=255), nullable=False),
        sa.Column("recipe_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("cron", sa.String(length=255), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipe_id", name="ingestion_recipe_schedule_recipe_uq"),
    )
    op.create_index(
        "ingestion_recipe_schedule_next_run_idx",
        "ingestion_recipe_schedule",
        ["next_run_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ingestion_recipe_schedule_next_run_idx", table_name="ingestion_recipe_schedule")
    op.drop_table("ingestion_recipe_schedule")
