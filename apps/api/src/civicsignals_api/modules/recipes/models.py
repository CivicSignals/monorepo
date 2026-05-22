"""recipes SQLAlchemy models.

Tables are prefixed ``recipes_`` and are migrated only by this module
(doc 06 §3, §4). Importing ``Base`` keeps Alembic autogenerate aware of this
module even before it had concrete tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
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
    server-side function (``gen_random_uuid()`` is core in Postgres 13+ but
    needs ``pgcrypto`` on older installs), so the table creates on a stock DB.
    """
    return uuid.uuid4()


class DeadLetter(Base):
    """A field/document that no extraction step could resolve (doc 18 §2.3, §3.4).

    The ordered fallback chain (primary → fallback → LLM-assisted) reached its
    end without a value, so the runner records the miss here rather than dropping
    it silently. The raw document lives in S3 (doc 18 §3.6), so once the recipe
    is fixed the extraction can be replayed against the snapshot identified by
    ``content_hash``. E7 (drift detection) reads these to decide when a recipe is
    degrading enough to auto-pause.
    """

    __tablename__ = "recipes_dead_letter"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=_new_uuid,
    )
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str] = mapped_column(String, nullable=False)
    # SHA-256 of the raw document body — the key to replay against the S3 snapshot.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False)
    # The ordered selector list we tried, for the human fixing the recipe.
    tried_selectors: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RunMetric(Base):
    """One recorded outcome of a recipe run — the cheap source for rolling drift
    metrics (doc 18 §3.2, TODO E7).

    The drift detector (doc 18 §3.2) needs rolling 24h/7d aggregates per recipe:
    extraction success rate, signals produced, LLM-fallback rate (from D11's
    :class:`DriftCounters`), and average wall clock. Computing those from the live
    extraction-job / candidate tables would mean cross-module joins on the busiest
    tables; instead, each completed run records *one* small row here from the run
    result. The rolling windows are then a single indexed aggregate over
    ``(recipe_id, finished_at)`` — cheap regardless of how big the job tables grow.

    This row is **derived bookkeeping**, not a source of truth: the raw documents
    (ingestion, S3) and the extraction jobs/candidates remain authoritative and
    replayable (doc 18 §3.6). A run that produced no extractions still records a
    row (``extractions_total = 0``) so an idle/failing recipe is visible.

    Owned solely by the ``recipes`` module (table prefix ``recipes_``); no FK
    across the module boundary — ``recipe_id`` is the recipe **slug string** (the
    runner's identity, doc 18 §3.5), same convention as ``recipes_dead_letter``.
    """

    __tablename__ = "recipes_run_metric"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False)
    recipe_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    # When the run finished — the timestamp the rolling windows slice on. Defaults
    # to now() but is set explicitly by the recorder so the window math is exact.
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # --- outcome tallies (denominators + numerators for the rates) ----------
    # Documents the run fetched/attempted to extract.
    documents_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Extraction attempts (one per document that reached the extract stage).
    extractions_total: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Extractions that produced a usable record (success — primary/fallback/LLM all
    # count; only a dead-letter/validation failure is a miss). Numerator of the
    # extraction success rate (doc 18 §3.2).
    extractions_succeeded: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # Canonical/candidate records produced — the "signals produced per run" metric.
    signals_produced: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Fields obtained via the LLM-assisted fallback rung (from D11 DriftCounters).
    llm_fallbacks: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Total fields attempted — the denominator for the LLM-fallback rate.
    fields_total: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Fields nothing could extract (dead-lettered) — surfaced for context.
    dead_letters: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    # Wall-clock seconds the run took, for the per-recipe wall-clock metric
    # (doc 18 §3.2). Nullable: a recorder that doesn't time the run leaves it null.
    wall_clock_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # The rolling windows aggregate per recipe over a recent time slice; this
        # composite index makes ``WHERE recipe_id = ? AND finished_at >= ?`` an
        # index range, not a seq scan, on a table that grows with every run.
        Index("ix_recipes_run_metric_recipe_finished", "recipe_id", "finished_at"),
    )


class DriftState(Base):
    """Per-recipe **drift bookkeeping** — the recipes module's record of a drift
    auto-pause + the GitHub-issue idempotency key (doc 18 §3.2, TODO E7).

    The *authoritative* scheduler pause flag lives in the **ingestion** module's
    ``ingestion_recipe_schedule`` (D4): that is what the cadence dispatcher reads to
    skip a recipe, and what :func:`ingestion.services.set_recipe_paused` flips.
    Module boundaries forbid recipes from importing ingestion (doc 06 §3), so the
    recipes drift logic does **not** own the scheduler pause. Instead it records its
    own *decision* here (``drift_paused`` + ``paused_reason`` for human context) and
    the URL of the issue it filed (``drift_issue_url``), and returns the decision so
    the ingestion beat task can apply the authoritative pause via ``set_recipe_paused``.

    ``drift_issue_url`` makes the auto-issue idempotent: once an issue is filed for a
    drift pause it is recorded here, so a later drift tick that still sees the recipe
    drifting does not open a duplicate. ``clear_drift_state`` (on unpause) clears it
    so the *next* breakage opens a fresh issue.

    One row per recipe (UNIQUE ``recipe_id``). Owned solely by the ``recipes`` module
    (table prefix ``recipes_``); ``recipe_id`` is the slug string, no cross-module FK.
    """

    __tablename__ = "recipes_drift_state"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    recipe_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # The recipes module's own record that drift decided to pause this recipe — NOT
    # the authoritative scheduler flag (that is ingestion_recipe_schedule.paused).
    drift_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Why drift paused it (e.g. "extraction success 0.31 < 0.50 over 24h") — context
    # for the human inspecting before unpausing. Null when never drift-paused.
    paused_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The GitHub issue opened for the current drift pause — idempotency key so a
    # repeated drift evaluation doesn't file duplicates. Cleared on unpause.
    drift_issue_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("recipe_id", name="recipes_drift_state_recipe_uq"),)
