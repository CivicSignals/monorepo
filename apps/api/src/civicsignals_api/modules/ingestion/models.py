"""ingestion SQLAlchemy models.

Tables are prefixed ``ingestion_`` and are migrated only by this module
(doc 06 §3, §4). Importing ``Base`` keeps Alembic autogenerate aware of this
module.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base


def _new_uuid() -> uuid.UUID:
    """Application-side UUID v4 default.

    Generating the PK in Python keeps the migration free of any dependency on a
    server-side UUID function (mirrors ``recipes._new_uuid``), so the table
    creates on a stock Postgres without ``pgcrypto``.
    """
    return uuid.uuid4()


class RawDocument(Base):
    """A raw fetched document + its provenance (doc 07 ``ingestion_raw_document``,
    doc 18 §2.2, §3.6; D3).

    The ingestion ``fetch`` step writes the raw bytes to S3 at a content-addressed
    key (``sha256/<content_hash>``) **before parsing**, then records this row.
    The bytes in S3 are the source of truth; the structured signal is derived and
    replayable against the snapshot keyed by ``content_hash`` once a recipe is
    fixed (doc 18 §3.6). Storage is global — multiple workspaces' signals can
    reference the same document — so this is *not* workspace-scoped (doc 07).

    Provenance pins exactly what produced the snapshot: which recipe + version,
    which connector, the source URL, when it was fetched, and the HTTP status.
    ``entity_id`` links the doc to the entity it came from when the recipe
    resolved one (nullable; resolution can be deferred to normalize). The
    ``recipe_id`` is stored as the recipe **slug string** (the runner's identity
    for a recipe — doc 18 §3.5), not a FK: recipes live as versioned YAML, there
    is no ``recipes_recipe`` table, and ingestion does not FK across the module
    boundary (doc 06 §3) — same convention as ``extraction_relevance_decision``.

    Dedupe: ``UNIQUE (recipe_id, content_hash)`` (doc 07) makes the row upsert
    idempotent — a re-fetch of unchanged content for the same recipe is a no-op.
    """

    __tablename__ = "ingestion_raw_document"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)

    # --- provenance ---------------------------------------------------------
    # Recipe slug + pinned version that produced this fetch (doc 18 §3.5).
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # Connector (source type) that did the fetching, e.g. ``http_static`` (doc 18 §1).
    connector: Mapped[str] = mapped_column(String(64), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    http_status: Mapped[int | None] = mapped_column(Integer)

    # --- content address ----------------------------------------------------
    # SHA-256 hex of the stored bytes — the key to replay against the S3 snapshot.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # S3 object key (``sha256/<content_hash>``) — where the bytes live.
    blob_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text)
    bytes_size: Mapped[int | None] = mapped_column(BigInteger)

    # --- entity link --------------------------------------------------------
    # The entity the doc came from, when the recipe resolved one (doc 18 §2.4).
    # Nullable: resolution can be deferred to normalize. SET NULL on entity
    # delete keeps the raw snapshot (the source of truth) even if the entity goes.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entities_entity.id", ondelete="SET NULL")
    )

    doc_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Dedupe-friendly: identical content for the same recipe collapses to one
        # row (doc 07). Same bytes from a *different* recipe still dedupe in S3
        # (same content-addressed key) but get their own provenance row here.
        UniqueConstraint("recipe_id", "content_hash", name="ingestion_raw_document_dedupe"),
        # Lookups by the entity a doc came from (doc 07 ``raw_document_entity_idx``).
        Index("ingestion_raw_document_entity_idx", "entity_id"),
        # Replay/extraction picks docs up by content hash (doc 18 §3.6).
        Index("ingestion_raw_document_content_hash_idx", "content_hash"),
    )
