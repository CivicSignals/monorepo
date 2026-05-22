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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import (
    CanonicalRecord,
    Fetcher,
)

from . import storage as storage_module
from .models import RawDocument
from .schemas import RawDocumentRef, StoredRawDocument
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
    bytes dedupe to one object regardless of source. The ``ingestion_raw_document``
    row is an **idempotent get-or-create keyed on** ``(recipe_id, content_hash)`` —
    re-storing the same content for the same recipe returns the *existing* row
    (``deduped=True``) without inserting a duplicate. It is deliberately **not** a
    field-refreshing upsert: the snapshot is immutable, so the original
    provenance (``fetched_at``, ``source_url``, ``http_status``, ``entity_id``,
    ``metadata``) is preserved on a re-fetch of byte-identical content rather than
    overwritten — that first-observed provenance is the one extraction replays
    against (doc 18 §3.6: the raw snapshot is the source of truth). Changed content
    produces a new hash and therefore a new row; that is how an update is recorded.

    Ordering keeps the work minimal and correct: the content hash/key are computed
    locally first, so an already-stored document short-circuits on a single SELECT
    with **no S3 write**. Only a genuinely new ``(recipe_id, content_hash)`` uploads
    (and even then ``put_document_if_absent`` HEADs first so identical bytes from a
    different recipe aren't re-PUT). The insert uses ``ON CONFLICT DO NOTHING`` and
    re-selects, so two ingest workers racing on the same content can't trip the
    UNIQUE constraint — the loser simply observes ``deduped=True``.

    The caller owns the transaction: this does **not** commit, so it composes
    inside a larger unit of work. ``fetched_at`` defaults to ``datetime.now(UTC)``
    (set explicitly for provenance rather than relying on the DB ``server_default``).
    """
    from datetime import UTC

    fetched_at = fetched_at or datetime.now(UTC)
    # Hash the bytes exactly once and reuse it for the lookup *and* the upload
    # (the storage methods accept ``precomputed_hash`` so large docs aren't
    # re-hashed).
    doc_hash = storage_module.content_hash(content)

    # Short-circuit: an existing row means we don't re-insert. But the S3 object
    # could have been deleted/expired out-of-band, which would leave ``blob_key``
    # unreadable — so still ensure the bytes are present (a no-op PUT when they
    # are), then return the existing provenance row (doc 18 §3.6).
    existing = await _find_by_recipe_and_hash(session, recipe_id, doc_hash)
    if existing is not None:
        storage.put_document_if_absent(
            content, content_type=content_type, precomputed_hash=doc_hash
        )
        return _to_schema(existing, deduped=True)

    # New content: upload (skipping the PUT if the object is already present) then
    # insert idempotently. ON CONFLICT DO NOTHING makes the SELECT->INSERT safe
    # under concurrent ingestion of identical content.
    stored = storage.put_document_if_absent(
        content, content_type=content_type, precomputed_hash=doc_hash
    )
    stmt = (
        pg_insert(RawDocument)
        .values(
            id=uuid.uuid4(),
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
        .on_conflict_do_nothing(constraint="ingestion_raw_document_dedupe")
        .returning(RawDocument.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()

    if inserted_id is None:
        # A concurrent writer won the race; re-select the row it inserted.
        existing = await _find_by_recipe_and_hash(session, recipe_id, doc_hash)
        assert existing is not None  # the conflicting row must exist post-insert
        return _to_schema(existing, deduped=True)

    row = await session.get(RawDocument, inserted_id)
    assert row is not None
    return _to_schema(row, deduped=False)


async def _find_by_recipe_and_hash(
    session: AsyncSession, recipe_id: str, content_hash: str
) -> RawDocument | None:
    return (
        await session.execute(
            select(RawDocument).where(
                RawDocument.recipe_id == recipe_id,
                RawDocument.content_hash == content_hash,
            )
        )
    ).scalar_one_or_none()


async def get_raw_document(
    session: AsyncSession, document_id: uuid.UUID
) -> StoredRawDocument | None:
    """Fetch a stored raw-document row by id (the read seam E1 picks docs up by).

    Returns the provenance row including ``blob_key`` so the extraction chain can
    pull the bytes back via ``RawDocumentStorage.get_document(blob_key)``.

    E1: the extraction orchestration chain reads stored documents back through here
    (and the matching ``storage.get_document(blob_key)``) to run the funnel against
    the S3 snapshot, replaying as recipes are fixed (doc 18 §2.3, §3.6). New
    documents are discovered for extraction via :func:`list_raw_document_refs`.
    """
    row = await session.get(RawDocument, document_id)
    return _to_schema(row, deduped=False) if row is not None else None


async def list_raw_document_refs(
    session: AsyncSession,
    *,
    limit: int = 200,
    after: datetime | None = None,
) -> list[RawDocumentRef]:
    """List stored raw documents oldest-first, for the extraction beat task (E1).

    ``extraction.run_pending_documents`` (every 1 min, doc 06 §8) calls this to
    find freshly fetched documents and create an ``extraction_job`` per document
    (idempotent — the job table's UNIQUE on ``raw_document_id`` makes a re-scan a
    no-op). ``after`` pages forward on ``created_at`` so a backlog is drained in
    bounded ticks rather than re-scanning the whole table each minute. Returns the
    lightweight :class:`RawDocumentRef` (id + recipe + cursor), not the full row or
    bytes — the per-document task loads those via :func:`get_raw_document`.
    """
    stmt = select(RawDocument.id, RawDocument.recipe_id, RawDocument.created_at)
    if after is not None:
        stmt = stmt.where(RawDocument.created_at > after)
    stmt = stmt.order_by(RawDocument.created_at).limit(limit)
    rows = (await session.execute(stmt)).all()
    return [
        RawDocumentRef(id=row.id, recipe_id=row.recipe_id, created_at=row.created_at)
        for row in rows
    ]


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
    "RawDocumentRef",
    "RawDocumentStorage",
    "StoredRawDocument",
    "crawl_recipe",
    "get_raw_document",
    "list_raw_document_refs",
    "store_raw_document",
]
