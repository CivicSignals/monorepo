"""signals SQLAlchemy models.

Tables are prefixed ``signals_`` and are migrated only by this module
(doc 06 §3, §4). Models import ``Base`` from the shared declarative base so
Alembic autogenerate sees one metadata object.

E4 lands ``signals_signal`` — the **canonical, global** signal (doc 07 §2
"signals", doc 14 §4.2). The extraction funnel (E1) promotes a validated typed
candidate into one row here; per-workspace scoring lives in a separate, much
smaller ``signals_workspace_score`` table (F3, doc 14 §5) — never on this row,
because a signal is global (one extraction → many subscribers, doc 07 §3).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from civicsignals_api.db import Base
from civicsignals_api.ids import uuid7

# pgvector embedding dimension (doc 07 §2: ``vector_embedding VECTOR(1536)``).
# The column is populated by I1 (embeddings); E4 only declares it nullable so I1
# does not need a follow-up migration just to add the column.
EMBEDDING_DIM = 1536

# Signal lifecycle status (doc 07 §4 "Signal" + doc 19 §6.3 confidence bands).
# E4 stores at ``new``/``pending_review``; F3 scoring and the feed move it onward.
# ``new`` — freshly promoted, confidence high enough to surface normally.
# ``pending_review`` — low-confidence (doc 19 §6.3 0.4-0.6 band) or entity
#   resolution pending (doc 19 §4.3); paid actions gated until reviewed.
SIGNAL_STATUS_NEW = "new"
SIGNAL_STATUS_PENDING_REVIEW = "pending_review"


class Signal(Base):
    """The canonical global signal (doc 07 §2 "signals", doc 14 §4.2).

    One row per distinct extracted signal, **not** workspace-scoped (doc 07 §3):
    the same Northshore RFP is one row regardless of how many workspaces it matches.
    Workspace relevance is the sparse ``signals_workspace_score`` table (F3, doc 14
    §5). The extraction funnel (E1) promotes a candidate that passed the strict
    per-type schema gate (E4 ``signals.schemas``) into this table via
    ``signals.services.promote_candidate_to_signal``.

    Columns map doc 07's ``signals_signal`` DDL:

    - ``entity_id`` — the resolved :class:`entities_entity` this signal is about.
      Nullable on purpose: entity resolution (doc 19 §4.3 / E10) can be *pending* —
      a failed resolve does not fail the pipeline; the signal is stored with
      ``entity_name_raw`` + ``review_required=True`` and re-linked once resolved.
    - ``signal_type`` — the MVP taxonomy slug (``signals.schemas.SignalType``).
    - ``recipe_id`` — the producing recipe slug (provenance; not a FK — recipes are
      versioned YAML, doc 06 §3).
    - ``extraction_job_id`` / ``source_candidate_id`` — loose refs back to the
      extraction provenance (doc 07's ``extraction_run_id`` — the run/job that
      produced it). Not cross-module FKs (doc 06 §3).
    - ``raw_document_ids`` — every ``ingestion_raw_document`` corroborating this
      signal (doc 19 §7.3 merge appends here). Stored as a UUID[]; not a FK array.
    - ``content_hash`` — the dedupe key (doc 07; the unique index below). E5 sets
      the real per-type canonical hash; E1's store path passes a placeholder.
    - ``occurred_at`` / ``observed_at`` — when the event happened vs when we saw it
      (doc 07). ``observed_at`` is required.
    - ``summary`` / ``title`` — the feed text + keyword-scoring surface (doc 14 §6.2).
    - ``details`` — the validated per-type payload (doc 07 ``details_jsonb``).
    - ``confidence`` — extraction confidence (doc 19 §6.2; E6 computes the blend,
      E1's store path passes the candidate's self-reported value).
    - ``status`` / ``is_degraded`` / ``review_required`` — the confidence-band flags
      (doc 19 §6.3).
    - ``vector_embedding`` — pgvector column for fuzzy dedupe + smart search
      (doc 07; populated by I1).

    Owned solely by the ``signals`` module (table prefix ``signals_``).
    """

    __tablename__ = "signals_signal"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid7)

    # Global FK to the entity directory (doc 07 §3). Nullable: resolution-pending
    # signals (doc 19 §4.3) keep the raw name and re-link when resolved (E10).
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entities_entity.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The raw entity string the extractor saw, kept when ``entity_id`` is unresolved.
    entity_name_raw: Mapped[str | None] = mapped_column(String(500), nullable=True)

    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Producing recipe slug (provenance; versioned YAML, not a FK — doc 06 §3).
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Extraction provenance (doc 07 ``extraction_run_id``). Loose refs, no FK across
    # the module boundary (doc 06 §3): the extraction job + candidate that produced
    # this signal. Nullable so a signal can be created from a non-pipeline path too.
    extraction_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Every corroborating raw document (doc 19 §7.3 merge appends). Stored as a JSONB
    # array of UUID *strings* (not a Postgres UUID[] column — JSONB keeps the merge
    # append + uniqueness simple and avoids a typed-array round-trip). Not a FK array:
    # ingestion owns ``ingestion_raw_document`` (doc 06 §3). ``SignalRead`` coerces
    # the strings back to ``uuid.UUID`` for the API.
    raw_document_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Dedupe key (doc 07 unique index). E5 computes the real per-type canonical hash.
    content_hash: Mapped[str] = mapped_column(String(128), nullable=False)

    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # The validated per-type payload (doc 07 ``details_jsonb``).
    details: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Extraction confidence (doc 19 §6.2). Nullable until E6's blend lands.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{SIGNAL_STATUS_NEW}'")
    )
    is_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    # pgvector embedding for fuzzy dedupe (doc 19 §7.4) + smart search (doc 07).
    # Populated by I1; the ANN (ivfflat) index is added by I1 once there is data to
    # tune ``lists`` against — declaring it here would build an empty, mistuned index.
    vector_embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # Dedupe (doc 07 ``signals_dedupe_idx``): the global uniqueness key. E5's
        # windowed dedupe (doc 19 §7) upserts on this; E1's store path relies on it
        # to make re-promotion of the same candidate idempotent.
        #
        # ``NULLS NOT DISTINCT`` (PG15+) is essential: ``entity_id`` is NULL for a
        # resolution-pending signal (doc 19 §4.3), and Postgres treats NULLs as
        # *distinct* in a unique index by default — which would let two identical
        # pending signals both insert (no dedupe). Treating NULLs as equal makes the
        # dedupe upsert fire for pending signals too.
        Index(
            "signals_dedupe_idx",
            "entity_id",
            "signal_type",
            "content_hash",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        # ICP candidate pre-filter + matching reads (doc 07 ``signals_entity_type_idx``,
        # doc 14 §6.1: narrow by entity + signal type).
        Index("signals_entity_type_idx", "entity_id", "signal_type"),
        # Feed ordering / recency (doc 07 ``signals_observed_at_idx``).
        Index("signals_observed_at_idx", "observed_at"),
    )
