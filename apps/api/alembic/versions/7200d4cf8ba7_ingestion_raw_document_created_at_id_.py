"""ingestion raw_document created_at,id index (E1 discovery)

Owned by the ``ingestion`` module (doc 06 §3, §4) — touches only ``ingestion_*``.
Adds the composite ``(created_at, id)`` index that backs the extraction beat
task's keyset-cursor discovery (``ingestion.services.list_raw_document_refs``, E1):
without it the every-1-min scan for newly-fetched documents is a sequential scan.
Chained after the E1 extraction tables to keep a single linear migration head.

Revision ID: 7200d4cf8ba7
Revises: 9838860e8504
Create Date: 2026-05-22 09:14:41.900617
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7200d4cf8ba7"
down_revision: str | None = "9838860e8504"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ingestion_raw_document_created_at_id_idx",
        "ingestion_raw_document",
        ["created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ingestion_raw_document_created_at_id_idx",
        table_name="ingestion_raw_document",
    )
