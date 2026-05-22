"""recipes run-metric + drift-state tables (E7 recipe drift detection)

Owned by the ``recipes`` module (doc 06 §3, §4) — touches only ``recipes_*``.

``recipes_run_metric`` records one small row per recipe run (extraction success
tallies, signals produced, LLM-fallback counts from D11's DriftCounters, wall
clock), so the rolling 24h/7d drift windows are a cheap indexed aggregate over
``(recipe_id, finished_at)`` instead of cross-module joins on the busy job tables
(doc 18 §3.2). ``recipes_drift_state`` holds the recipes-side drift bookkeeping —
the ``drift_paused`` decision record + the ``drift_issue_url`` idempotency key for
the auto-opened GitHub issue. The *authoritative* scheduler pause that stops new
runs lives in ingestion's ``ingestion_recipe_schedule.paused`` (D4); auto-pause
stops only new runs and past signals stay visible.

Both PKs are generated application-side (``models._new_uuid``), so this migration
needs no server-side UUID function / pgcrypto. The recipes module only creates
``recipes_*`` tables; chaining it after the current head keeps a single linear
migration head (the tables are independent, so the order is immaterial).

Revision ID: 785be61c50d1
Revises: 479ad775f192
Create Date: 2026-05-22 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "785be61c50d1"
down_revision: str | None = "479ad775f192"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipes_run_metric",
        # PK populated application-side (models._new_uuid) — no server-side UUID fn.
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recipe_id", sa.String(length=255), nullable=False),
        sa.Column("recipe_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("documents_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("extractions_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "extractions_succeeded",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("signals_produced", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("llm_fallbacks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("fields_total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dead_letters", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("wall_clock_seconds", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recipes_run_metric_recipe_finished",
        "recipes_run_metric",
        ["recipe_id", "finished_at"],
        unique=False,
    )

    # The recipes-side drift bookkeeping (issue idempotency + the drift-pause
    # record). The *authoritative* scheduler pause flag lives in ingestion's
    # ``ingestion_recipe_schedule`` (D4) — this table only records the recipes
    # module's drift decision + the GitHub-issue idempotency key.
    op.create_table(
        "recipes_drift_state",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recipe_id", sa.String(length=255), nullable=False),
        sa.Column("drift_paused", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("paused_reason", sa.Text(), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drift_issue_url", sa.Text(), nullable=True),
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
        sa.UniqueConstraint("recipe_id", name="recipes_drift_state_recipe_uq"),
    )


def downgrade() -> None:
    op.drop_table("recipes_drift_state")
    op.drop_index("ix_recipes_run_metric_recipe_finished", table_name="recipes_run_metric")
    op.drop_table("recipes_run_metric")
