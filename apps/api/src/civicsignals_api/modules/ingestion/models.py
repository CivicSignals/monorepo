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
    Boolean,
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
        # The extraction beat task (E1) pages new docs oldest-first on the keyset
        # cursor (created_at, id) via ``services.list_raw_document_refs``; this
        # composite index makes that scan an index range, not a seq scan.
        Index("ingestion_raw_document_created_at_id_idx", "created_at", "id"),
    )


class RecipeSchedule(Base):
    """Per-recipe scheduling run-state for the cadence dispatcher (D4; doc 18 §3, §6).

    The ``scheduler`` process (Celery beat, a leader-elected singleton — doc 06 §8,
    doc 18 §6.1) runs the ``ingestion.dispatch_due_recipes`` beat task every minute.
    Each tick it loads the active recipes (YAML under ``recipes/``) and, for each,
    decides whether it is *due* by comparing ``now`` against this row's
    ``next_run_at`` (computed from the recipe's ``schedule.cron`` plus a per-recipe
    deterministic jitter window, doc 18 §6.4 "adaptive scheduling"). A due recipe is
    enqueued as ``ingestion.crawl_recipe`` on the ``ingest`` queue and its
    ``last_*`` / ``next_run_at`` are advanced here so the next tick doesn't re-fire
    it (doc 06 §8 per-recipe cadence ~5 min to 24 h).

    Keyed by the recipe **slug string** — recipes are versioned YAML, there is no
    ``recipes_recipe`` table, and ingestion does not FK across the module boundary
    (doc 06 §3; same convention as ``ingestion_raw_document.recipe_id``). The pinned
    ``recipe_version`` last dispatched is recorded for provenance (doc 18 §3.5).
    """

    __tablename__ = "ingestion_recipe_schedule"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)

    # Recipe slug — the dispatcher's identity for a recipe (doc 18 §3.5). One
    # schedule row per recipe (UNIQUE) so ``next_run_at`` is unambiguous.
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # The pinned recipe_version last dispatched (provenance, doc 18 §3.5).
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    # The recipe's cron expression as last seen — recorded so an edit to the
    # recipe's cadence is observable and the next due-time can be recomputed.
    cron: Mapped[str | None] = mapped_column(String(255))

    # When the dispatcher last enqueued a crawl for this recipe (NULL = never run).
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The earliest wall-clock time the next crawl may be enqueued. The due check is
    # ``now >= next_run_at`` (a NULL/absent row means due immediately). Includes the
    # per-recipe jitter offset so recipes sharing a cron don't fire in lockstep.
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Operator pause switch (doc 18 §3.2 auto-pause / manual pause): a paused recipe
    # is skipped by the dispatcher but its past signals stay visible (doc 18 §3.2).
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # One schedule row per recipe — the get-or-create the dispatcher upserts on.
        UniqueConstraint("recipe_id", name="ingestion_recipe_schedule_recipe_uq"),
        # The dispatcher scans for due rows by ``next_run_at <= now`` each tick.
        Index("ingestion_recipe_schedule_next_run_idx", "next_run_at"),
    )
