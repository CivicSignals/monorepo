"""M3 foia_attachment — uploaded FOIA response docs + extraction pipeline linkage.

Revision ID: 5deb42185a35
Revises: b282915c8b8f
Create Date: 2026-05-22 14:00:00.000000

Adds the ``foia_attachment`` table (prefixed ``foia_``, owned by the foia module
per doc 06 §3). One row per uploaded response document; carries loose refs to
``ingestion_raw_document`` (D3) and to the ``extraction_job`` (E1) — both stored
as plain UUID columns, *not* cross-module FKs (doc 06 §3).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5deb42185a35"
down_revision: str | None = "b282915c8b8f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ### M3: foia_attachment table ###
    op.create_table(
        "foia_attachment",
        sa.Column("id", sa.UUID(), nullable=False),
        # FK to foia_request (CASCADE delete — no dangling attachments).
        sa.Column("foia_request_id", sa.UUID(), nullable=False),
        # Loose ref to ingestion_raw_document (no cross-module FK — doc 06 §3).
        sa.Column("raw_document_id", sa.UUID(), nullable=False),
        # Loose ref to extraction_job (no cross-module FK).
        sa.Column("extraction_job_id", sa.UUID(), nullable=True),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        # FK to accounts_user (RESTRICT — keep attachment if user is deleted).
        sa.Column("uploaded_by", sa.UUID(), nullable=False),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "extraction_status",
            sa.Enum(
                "pending", "running", "done", "failed", "skipped",
                name="foia_attachment_extraction_status",
                native_enum=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        # Primary key.
        sa.PrimaryKeyConstraint("id"),
        # FK to foia_request.
        sa.ForeignKeyConstraint(
            ["foia_request_id"],
            ["foia_request.id"],
            ondelete="CASCADE",
        ),
        # FK to accounts_user (the uploader).
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["accounts_user.id"],
            ondelete="RESTRICT",
        ),
        # extraction_status must be one of the defined values.
        sa.CheckConstraint(
            "extraction_status IN ('pending', 'running', 'done', 'failed', 'skipped')",
            name="ck_foia_attachment_extraction_status",
        ),
    )

    op.create_index(
        "ix_foia_attachment_request_id",
        "foia_attachment",
        ["foia_request_id"],
    )
    op.create_index(
        "ix_foia_attachment_raw_document_id",
        "foia_attachment",
        ["raw_document_id"],
    )
    op.create_index(
        "ix_foia_attachment_uploaded_by",
        "foia_attachment",
        ["uploaded_by"],
    )
    # ### end M3 ###


def downgrade() -> None:
    op.drop_index("ix_foia_attachment_uploaded_by", table_name="foia_attachment")
    op.drop_index("ix_foia_attachment_raw_document_id", table_name="foia_attachment")
    op.drop_index("ix_foia_attachment_request_id", table_name="foia_attachment")
    op.drop_table("foia_attachment")
