"""Pydantic request/response shapes for the ingestion module (doc 06 §3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RawDocumentRef(BaseModel):
    """A lightweight reference to a stored raw document (E1 discovery seam).

    Just the identity + provenance the extraction beat task
    (``run_pending_documents``) needs to create an ``extraction_job`` for a freshly
    fetched document, without loading the full row or the bytes. ``created_at`` is
    the cursor the task pages forward on (oldest first).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipe_id: str
    created_at: datetime


class StoredRawDocument(BaseModel):
    """A persisted ``ingestion_raw_document`` row + its S3 content address (D3).

    Returned by ``services.store_raw_document`` so a caller (the ingest worker,
    or E1's extraction chain) gets back both the database identity and where the
    bytes live, without reaching into the ORM model across the module boundary
    (doc 06 §3). ``deduped`` reports whether this call reused an existing row
    (idempotent on ``(recipe_id, content_hash)``) rather than inserting a new one.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    recipe_id: str
    recipe_version: int
    connector: str
    source_url: str
    fetched_at: datetime
    http_status: int | None = None
    content_hash: str
    blob_key: str
    content_type: str | None = None
    bytes_size: int | None = None
    entity_id: uuid.UUID | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    # True when the row already existed and was returned as-is (no new insert).
    deduped: bool = False


class RecipeScheduleState(BaseModel):
    """The scheduler's run-state for one recipe (``ingestion_recipe_schedule``; D4).

    Read by the cadence dispatcher each beat tick to decide whether a recipe is due
    (``now >= next_run_at``) and written back after a crawl is enqueued (advancing
    ``last_run_at`` / ``next_run_at``). Exposed as a Pydantic view so the dispatcher
    + tests don't reach into the ORM model across the module boundary (doc 06 §3).
    """

    model_config = ConfigDict(from_attributes=True)

    recipe_id: str
    recipe_version: int
    cron: str | None = None
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    paused: bool = False
