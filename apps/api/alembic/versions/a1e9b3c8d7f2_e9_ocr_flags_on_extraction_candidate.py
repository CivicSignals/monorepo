"""E9 OCR flags on extraction_candidate (E9)

Adds two boolean columns to ``extraction_candidate`` to record whether the OCR
fallback ran during the parse stage (doc 19 §2.2):

- ``ocr_used``      — True when pdfplumber returned < 200 chars for a > 5-page PDF
                      and the OCR backend (Tesseract/Textract) was invoked.
- ``ocr_truncated`` — True when the PDF had > 100 pages and only the first-50 +
                      last-25 pages were OCR'd.

Both default ``false`` (all existing rows pre-date E9; no OCR was attempted on them).

Owned by the ``extraction`` module (doc 06 §3) — touches only ``extraction_candidate``.
Chains after the E9 heads merge (3753b6a9ea3f).

Revision ID: a1e9b3c8d7f2
Revises: 3753b6a9ea3f
Create Date: 2026-05-22 12:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1e9b3c8d7f2"
down_revision: str | None = "3753b6a9ea3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add E9 OCR flags to extraction_candidate (doc 19 §2.2).
    # Both columns default false so the upgrade is safe to apply online:
    # existing rows pre-date OCR; ``false`` is the correct semantic default.
    op.add_column(
        "extraction_candidate",
        sa.Column(
            "ocr_used",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "extraction_candidate",
        sa.Column(
            "ocr_truncated",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("extraction_candidate", "ocr_truncated")
    op.drop_column("extraction_candidate", "ocr_used")
