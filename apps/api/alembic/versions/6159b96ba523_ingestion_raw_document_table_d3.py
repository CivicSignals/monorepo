"""ingestion raw document table (D3)

Owned by the ``ingestion`` module (doc 06 §3, §4) — touches only
``ingestion_raw_document``. The content-addressable raw-document store: each row
records a fetched document's provenance (recipe slug + version, connector, source
URL, fetched_at, HTTP status) and where its bytes live in S3 (``blob_key``,
``content_hash``), with a nullable FK to ``entities_entity`` for the entity the
doc came from (doc 07 ``ingestion_raw_document``, doc 18 §2.2, §3.6).

``UNIQUE (recipe_id, content_hash)`` makes the row upsert idempotent — a re-fetch
of unchanged content for the same recipe dedupes (doc 07). The ``id`` PK is
generated application-side (``models._new_uuid``), so this migration needs no
server-side UUID function. Chains linearly off the entities head
(``f8de6b788783``); the new table only depends on ``entities_entity`` existing.

Revision ID: 6159b96ba523
Revises: f8de6b788783
Create Date: 2026-05-22 06:57:55.902504
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6159b96ba523"
down_revision: str | None = "f8de6b788783"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_raw_document",
        # PK is populated application-side (models._new_uuid), so no server-side
        # UUID function / pgcrypto extension is needed.
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("recipe_id", sa.String(length=255), nullable=False),
        sa.Column("recipe_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("connector", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("blob_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("bytes_size", sa.BigInteger(), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities_entity.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recipe_id", "content_hash", name="ingestion_raw_document_dedupe"),
    )
    op.create_index(
        "ingestion_raw_document_content_hash_idx",
        "ingestion_raw_document",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        "ingestion_raw_document_entity_idx",
        "ingestion_raw_document",
        ["entity_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ingestion_raw_document_entity_idx", table_name="ingestion_raw_document")
    op.drop_index("ingestion_raw_document_content_hash_idx", table_name="ingestion_raw_document")
    op.drop_table("ingestion_raw_document")
