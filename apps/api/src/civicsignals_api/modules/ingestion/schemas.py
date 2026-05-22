"""Pydantic request/response shapes for the ingestion module (doc 06 §3)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
