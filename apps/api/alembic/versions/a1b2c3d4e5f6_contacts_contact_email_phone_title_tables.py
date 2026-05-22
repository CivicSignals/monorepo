"""contacts: contact, email, phone, title tables

Creates the global contact directory (C2, doc 07 §2): per-person contact rows
for public-sector entities, with split email/phone/title child tables each
carrying per-record source provenance (doc 16 §18). Touches only ``contacts_*``
objects (doc 06 §4 ownership). Chained onto the entities migration (f8de6b788783)
so Alembic history stays linear (single head — see MIGRATION RULE).

Revision ID: a1b2c3d4e5f6
Revises: f8de6b788783
Create Date: 2026-05-22 06:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
# Chained onto C1 entities migration (single head, per MIGRATION RULE).
down_revision: str | None = "f8de6b788783"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Shared provenance columns (doc 16 §18) — added to each child table.
# ---------------------------------------------------------------------------

_PROVENANCE_COLS = [
    sa.Column("source", sa.Text(), nullable=True),
    sa.Column("source_url", sa.Text(), nullable=True),
    sa.Column("source_recipe_id", sa.UUID(), nullable=True),
    sa.Column("confidence", sa.Double(), nullable=True),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
]


def upgrade() -> None:
    # ### contacts_contact — person at a public-sector entity (C2 req 1) ###
    op.create_table(
        "contacts_contact",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("canonical_email", sa.Text(), nullable=True),
        # Provenance columns (doc 16 §18).
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_recipe_id", sa.UUID(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        # Attributes blob.
        sa.Column(
            "attributes",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'stale')",
            name="contacts_contact_status_check",
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities_entity.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Non-unique entity index for fast lookups.
    op.create_index("contacts_contact_entity_idx", "contacts_contact", ["entity_id"], unique=False)
    # Status index for stale/inactive filtering.
    op.create_index("contacts_contact_status_idx", "contacts_contact", ["status"], unique=False)
    # Partial unique index: (entity_id, canonical_email) where canonical_email IS NOT NULL.
    # Drives idempotent upsert (doc 07 §2 UNIQUE(entity_id, email)).
    op.create_index(
        "contacts_contact_entity_email_uniq_idx",
        "contacts_contact",
        ["entity_id", "canonical_email"],
        unique=True,
        postgresql_where=sa.text("canonical_email IS NOT NULL"),
    )

    # ### contacts_email — email addresses with per-record provenance ###
    op.create_table(
        "contacts_email",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "email_status",
            sa.String(length=16),
            server_default=sa.text("'unverified'"),
            nullable=False,
        ),
        # Provenance columns (doc 16 §18).
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_recipe_id", sa.UUID(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "email_status IN ('unverified', 'valid', 'risky', 'invalid', 'stale')",
            name="contacts_email_status_check",
        ),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts_contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("contacts_email_contact_idx", "contacts_email", ["contact_id"], unique=False)
    op.create_index(
        "contacts_email_contact_email_idx",
        "contacts_email",
        ["contact_id", "email"],
        unique=True,
    )

    # ### contacts_phone — phone numbers with per-record provenance ###
    op.create_table(
        "contacts_phone",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("phone_type", sa.String(length=32), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        # Provenance columns (doc 16 §18).
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_recipe_id", sa.UUID(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["contact_id"], ["contacts_contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("contacts_phone_contact_idx", "contacts_phone", ["contact_id"], unique=False)
    op.create_index(
        "contacts_phone_contact_phone_idx",
        "contacts_phone",
        ["contact_id", "phone"],
        unique=True,
    )

    # ### contacts_title — title/position history with per-record provenance ###
    op.create_table(
        "contacts_title",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("contact_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("department", sa.Text(), nullable=True),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        # Provenance columns (doc 16 §18).
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_recipe_id", sa.UUID(), nullable=True),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["contact_id"], ["contacts_contact.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("contacts_title_contact_idx", "contacts_title", ["contact_id"], unique=False)
    op.create_index(
        "contacts_title_contact_current_idx",
        "contacts_title",
        ["contact_id", "is_current"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("contacts_title_contact_current_idx", table_name="contacts_title")
    op.drop_index("contacts_title_contact_idx", table_name="contacts_title")
    op.drop_table("contacts_title")

    op.drop_index("contacts_phone_contact_phone_idx", table_name="contacts_phone")
    op.drop_index("contacts_phone_contact_idx", table_name="contacts_phone")
    op.drop_table("contacts_phone")

    op.drop_index("contacts_email_contact_email_idx", table_name="contacts_email")
    op.drop_index("contacts_email_contact_idx", table_name="contacts_email")
    op.drop_table("contacts_email")

    op.drop_index(
        "contacts_contact_entity_email_uniq_idx",
        table_name="contacts_contact",
        postgresql_where=sa.text("canonical_email IS NOT NULL"),
    )
    op.drop_index("contacts_contact_status_idx", table_name="contacts_contact")
    op.drop_index("contacts_contact_entity_idx", table_name="contacts_contact")
    op.drop_table("contacts_contact")
