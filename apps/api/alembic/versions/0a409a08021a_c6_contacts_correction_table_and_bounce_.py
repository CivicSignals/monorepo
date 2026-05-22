"""c6_contacts_correction_table_and_bounce_fields

Adds:
- ``contacts_contact.reported_invalid_at`` — timestamp of last correction report.
- ``contacts_contact.bounce_count`` — cumulative bounce/invalid report count.
- ``contacts_correction`` — workspace-scoped audit log of correction reports (C6).
  Each row records WHO reported a contact as invalid/bounced/wrong, in WHICH
  workspace, WHAT kind of problem, and an optional free-text reason/correction.
  The contact row is updated in-place; this table is the append-only audit trail.

Revision ID: 0a409a08021a
Revises: bb5285cd2571
Create Date: 2026-05-22 10:03:03.774468
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a409a08021a"
down_revision: str | None = "718a00f802f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- contacts_correction table (C6) ---
    op.create_table(
        "contacts_correction",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("reporter_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            sa.String(length=32),
            server_default=sa.text("'other'"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("correction", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('bounced', 'wrong_email', 'wrong_phone', 'wrong_person', 'other')",
            name="contacts_correction_kind_check",
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["contacts_contact.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "contacts_correction_contact_idx",
        "contacts_correction",
        ["contact_id"],
        unique=False,
    )
    op.create_index(
        "contacts_correction_workspace_idx",
        "contacts_correction",
        ["workspace_id"],
        unique=False,
    )

    # --- contacts_contact: add C6 correction tracking columns ---
    op.add_column(
        "contacts_contact",
        sa.Column(
            "reported_invalid_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "contacts_contact",
        sa.Column(
            "bounce_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("contacts_contact", "bounce_count")
    op.drop_column("contacts_contact", "reported_invalid_at")
    op.drop_index("contacts_correction_workspace_idx", table_name="contacts_correction")
    op.drop_index("contacts_correction_contact_idx", table_name="contacts_correction")
    op.drop_table("contacts_correction")
