"""m2_foia_request_crud_and_state_machine

Revision ID: 5d3748bed3c5
Revises: 467a515ea5a6
Create Date: 2026-05-22 08:38:57.002946

Creates the two M2 foia tables:
- ``foia_request``   — workspace-scoped FOIA request with state machine.
- ``foia_request_event`` — immutable status-transition history.

The state machine statuses (draft / sent / ack / response) are enforced by a
CHECK constraint. The service layer also enforces them (doc 06 §3); the DB
constraint is a safety net.

Depends on (via FK):
- ``accounts_workspace``  (B5)
- ``accounts_user``       (B1)
- ``entities_entity``     (C1)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d3748bed3c5"
down_revision: str | None = "467a515ea5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "foia_request",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=32), nullable=True),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "submission_method",
            sa.Enum(
                "manual",
                "email",
                "portal",
                "mail",
                "in_person",
                name="foia_submission_method",
                native_enum=False,
                length=16,
            ),
            server_default="manual",
            nullable=False,
        ),
        sa.Column("submission_target", sa.String(length=1024), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "sent",
                "ack",
                "response",
                name="foia_request_status",
                native_enum=False,
                length=16,
            ),
            server_default="draft",
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ack_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_notes", sa.Text(), nullable=True),
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
            "status IN ('draft', 'sent', 'ack', 'response')",
            name="ck_foia_request_status",
        ),
        sa.CheckConstraint(
            "submission_method IN ('manual', 'email', 'portal', 'mail', 'in_person')",
            name="ck_foia_request_submission_method",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["accounts_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities_entity.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["accounts_workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_foia_request_created_by", "foia_request", ["created_by"], unique=False)
    op.create_index("ix_foia_request_entity_id", "foia_request", ["entity_id"], unique=False)
    op.create_index("ix_foia_request_workspace_id", "foia_request", ["workspace_id"], unique=False)
    op.create_index(
        "ix_foia_request_workspace_status",
        "foia_request",
        ["workspace_id", "status"],
        unique=False,
    )

    op.create_table(
        "foia_request_event",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("actor_id", sa.UUID(), nullable=False),
        sa.Column("from_status", sa.String(length=16), nullable=False),
        sa.Column("to_status", sa.String(length=16), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["accounts_user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["request_id"], ["foia_request.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_foia_request_event_request_id",
        "foia_request_event",
        ["request_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_foia_request_event_request_id", table_name="foia_request_event")
    op.drop_table("foia_request_event")
    op.drop_index("ix_foia_request_workspace_status", table_name="foia_request")
    op.drop_index("ix_foia_request_workspace_id", table_name="foia_request")
    op.drop_index("ix_foia_request_entity_id", table_name="foia_request")
    op.drop_index("ix_foia_request_created_by", table_name="foia_request")
    op.drop_table("foia_request")
