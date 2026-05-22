"""searches SQLAlchemy models.

Tables are prefixed ``searches_`` and are migrated only by this module
(doc 06 §3, §4). A *saved search* (H1) captures the G1 feed filter set so a user
can re-run the same filtered view of their workspace feed, and so a digest (H3)
can later be scheduled against it.

The shape mirrors the workspace-scoped CRUD pattern (cf. ``icp_definition``):

- ``workspace_id`` is the tenant boundary — every read/write is scoped through it
  (the routes resolve it from ``X-Workspace-Id`` via ``require_workspace``).
- ``created_by`` is the user who owns the search. Owners can edit/delete their
  own searches; **shared** searches are visible (read-only to non-owners) to the
  whole workspace (doc 08 §1.4, the H1 "share within workspace" rule).
- ``filters`` is the validated subset of the G1 feed filter params
  (``signals.list_workspace_signals``) stored as JSONB. The schema layer validates
  the payload before it lands here so the digest runner (H3) and a future feed
  "apply saved search" path can trust the stored values. (# TODO H2: richer
  filter validation — e.g. keyword / entity facets — extends this blob.)
- ``is_shared`` is the workspace-visibility toggle. The MVP keeps sharing as a
  simple boolean (visible to all members vs. private to the owner); a richer
  visibility enum can replace it without a data migration of the filter blob.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7


class SavedSearch(Base):
    """A saved feed filter set, workspace-scoped (H1; doc 14 §8).

    Owned by the user who created it; optionally shared so the rest of the
    workspace can see (but not edit) it. Listing is workspace-scoped and includes
    the caller's own searches plus every shared search in the workspace.
    """

    __tablename__ = "searches_saved_search"
    __table_args__ = (
        # The list query filters by workspace and then orders/keysets on ``id``
        # (UUID v7, time-ordered). A composite index keeps the per-workspace scan
        # cheap as the table grows across tenants.
        Index("ix_searches_saved_search_workspace_id", "workspace_id"),
        Index("ix_searches_saved_search_created_by", "created_by"),
        # The "shared searches visible to the workspace" read filters on
        # (workspace_id, is_shared); a partial index serves it directly.
        Index(
            "ix_searches_saved_search_workspace_shared",
            "workspace_id",
            postgresql_where=text("is_shared"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_workspace.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The user who created and owns this saved search. RESTRICT so a user cannot
    # be hard-deleted while they still own searches (audit safety).
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts_user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # The validated subset of the G1 feed filter params (doc 14 §5.3). An empty
    # object ``{}`` means "the default feed" (no extra filters).
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_shared: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
