"""recipes dead-letter table (D11)

Owned by the ``recipes`` module (doc 06 §3, §4) — touches only ``recipes_*``.
Records fields/documents the ordered fallback chain (primary → fallback →
LLM-assisted) could not extract, so a miss is durably visible and replayable
against the S3 raw-document snapshot keyed by ``content_hash`` (doc 18 §2.3,
§3.4, §3.6). ``gen_random_uuid()`` is built into Postgres 13+.

Revision ID: 91d6709f6982
Revises:
Create Date: 2026-05-22 05:19:47.833473
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "91d6709f6982"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipes_dead_letter",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("recipe_id", sa.String(length=255), nullable=False),
        sa.Column("recipe_version", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("field_name", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column(
            "tried_selectors",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_recipes_dead_letter_content_hash"),
        "recipes_dead_letter",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_recipes_dead_letter_recipe_id"),
        "recipes_dead_letter",
        ["recipe_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_recipes_dead_letter_recipe_id"), table_name="recipes_dead_letter")
    op.drop_index(op.f("ix_recipes_dead_letter_content_hash"), table_name="recipes_dead_letter")
    op.drop_table("recipes_dead_letter")
