"""Public service interface for the ingestion module.

Other modules call ingestion only through the functions defined here — never by
importing ingestion's models or routes directly (doc 06 §3).

Ingestion orchestrates *crawl runs*: it owns scheduling, the pending-document
queue, and raw-document storage (TODO D3/D4). The recipe **DSL + runner** live
in the recipes module (TODO D1) — ingestion drives them through that module's
``services.py`` rather than reaching into the runner internals (the cross-module
seam, doc 06 §3). The concrete connector fetchers it injects are TODO D6.

Raw-document storage (D3): :func:`store_raw_document` writes the fetched bytes to
S3 at a content-addressed key and upserts an ``ingestion_raw_document`` row with
provenance. It is idempotent on ``(recipe_id, content_hash)`` so a re-fetch of
unchanged content is a no-op. The recipe runner's ``fetch`` step (D1) does not
itself touch the database, so persistence is wired here (and is the seam E1's
extraction chain reads stored documents back through).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import (
    CanonicalRecord,
    Fetcher,
)

from .models import RawDocument
from .schemas import StoredRawDocument
from .storage import RawDocumentStorage


def crawl_recipe(
    recipe_id: str,
    fetcher: Fetcher,
    seed_urls: Sequence[str],
) -> list[CanonicalRecord]:
    """Run one recipe's full lifecycle (doc 18 §2; TODO D4 schedules this).

    Thin delegation to the recipes module's runner. The ``fetcher`` is supplied
    by the connector selected for the recipe (TODO D6); raw-document persistence
    to S3 wraps the fetch step via :func:`store_raw_document` (D3), wired into the
    crawl loop in TODO D4.
    """
    return recipes_services.run_recipe(recipe_id, fetcher, seed_urls)


async def store_raw_document(
    session: AsyncSession,
    storage: RawDocumentStorage,
    *,
    content: bytes,
    recipe_id: str,
    connector: str,
    source_url: str,
    recipe_version: int = 1,
    content_type: str = "application/octet-stream",
    fetched_at: datetime | None = None,
    http_status: int | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: dict[str, object] | None = None,
) -> StoredRawDocument:
    """Persist a fetched document: bytes -> S3, provenance -> DB (doc 18 §2.2; D3).

    Content-addressable: the S3 key is ``sha256/<content_hash>``, so identical
    bytes dedupe to one object regardless of source. The
    ``ingestion_raw_document`` row is **idempotent on** ``(recipe_id,
    content_hash)`` — re-storing the same content for the same recipe returns the
    existing row (``deduped=True``) without inserting a duplicate or re-uploading.
    This makes a re-fetch of unchanged content cheap and safe (doc 18 §3.6: the
    raw snapshot is the source of truth and extraction is replayable against it).

    The caller owns the transaction: on a new insert this flushes (to populate
    ``id``/defaults) but does **not** commit, so it composes inside a larger unit
    of work. ``fetched_at`` defaults to ``datetime.now`` (UTC-aware via the DB
    ``server_default`` is bypassed because we set it explicitly for provenance).
    """
    from datetime import UTC

    fetched_at = fetched_at or datetime.now(UTC)
    stored = storage.put_document(content, content_type=content_type)

    # Idempotent on (recipe_id, content_hash): a re-fetch of unchanged content
    # for the same recipe reuses the existing provenance row.
    existing = (
        await session.execute(
            select(RawDocument).where(
                RawDocument.recipe_id == recipe_id,
                RawDocument.content_hash == stored.content_hash,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _to_schema(existing, deduped=True)

    row = RawDocument(
        recipe_id=recipe_id,
        recipe_version=recipe_version,
        connector=connector,
        source_url=source_url,
        fetched_at=fetched_at,
        http_status=http_status,
        content_hash=stored.content_hash,
        blob_key=stored.key,
        content_type=stored.content_type,
        bytes_size=stored.size,
        entity_id=entity_id,
        doc_metadata=metadata or {},
    )
    session.add(row)
    # Flush (not commit) to populate id + server defaults while letting the
    # caller own the surrounding transaction boundary.
    await session.flush()
    return _to_schema(row, deduped=False)


async def get_raw_document(
    session: AsyncSession, document_id: uuid.UUID
) -> StoredRawDocument | None:
    """Fetch a stored raw-document row by id (the read seam E1 picks docs up by).

    Returns the provenance row including ``blob_key`` so the extraction chain can
    pull the bytes back via ``RawDocumentStorage.get_document(blob_key)``.

    # TODO E1: the extraction orchestration chain reads stored documents back
    # through here (and the matching ``storage.get_document(blob_key)``) to run
    # ``extract -> normalize`` against the S3 snapshot, replaying as recipes are
    # fixed (doc 18 §2.3, §3.6).
    """
    row = await session.get(RawDocument, document_id)
    return _to_schema(row, deduped=False) if row is not None else None


def _to_schema(row: RawDocument, *, deduped: bool) -> StoredRawDocument:
    return StoredRawDocument(
        id=row.id,
        recipe_id=row.recipe_id,
        recipe_version=row.recipe_version,
        connector=row.connector,
        source_url=row.source_url,
        fetched_at=row.fetched_at,
        http_status=row.http_status,
        content_hash=row.content_hash,
        blob_key=row.blob_key,
        content_type=row.content_type,
        bytes_size=row.bytes_size,
        entity_id=row.entity_id,
        metadata=dict(row.doc_metadata),
        deduped=deduped,
    )


__all__ = [
    "CanonicalRecord",
    "Fetcher",
    "RawDocumentStorage",
    "StoredRawDocument",
    "crawl_recipe",
    "get_raw_document",
    "store_raw_document",
]
