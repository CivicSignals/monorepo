"""DB-backed tests for raw-document persistence (D3 req 2, req 3).

The ``store_raw_document`` service writes bytes to S3 (mocked here with ``moto``)
and upserts the ``ingestion_raw_document`` row. These assertions need a real
Postgres (the row uses a JSONB column + a UUID FK to ``entities_entity``), so
they run only when a DSN is configured — resolved from (in order)
``INGESTION_TEST_DSN``, ``DATABASE_DIRECT_URL``, or ``DATABASE_URL`` — and skip
cleanly otherwise. Same opt-in-via-env convention as the entities C1 tests, so
they execute automatically in CI (which sets ``DATABASE_DIRECT_URL``).

Each test creates only ``ingestion_raw_document`` + ``entities_entity`` (the FK
target) from the SQLAlchemy metadata and drops them after — it never touches
another module's tables.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import boto3
import pytest
import pytest_asyncio
from moto import mock_aws
from sqlalchemy import Table, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from civicsignals_api.db import Base

# Importing the entities model registers the FK target table (``entities_entity``)
# on Base's metadata before the fixture creates tables.
from civicsignals_api.modules.entities.models import Entity
from civicsignals_api.modules.ingestion import services
from civicsignals_api.modules.ingestion.models import RawDocument
from civicsignals_api.modules.ingestion.storage import RawDocumentStorage, StoredObject

_DSN = (
    os.environ.get("INGESTION_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

# Tables this test needs — never drop another module's *unrelated* tables. We
# create the whole entities trio because ``entities_entity`` (the raw-document FK
# target) itself FKs into ``entities_kind`` / ``entities_geo``, so creating it
# alone would fail. ``SQLAlchemy`` create_all/drop_all sort by FK dependency
# regardless of list order, so we pass the set and let it order correctly.
_TABLE_NAMES = (
    "entities_kind",
    "entities_geo",
    "entities_entity",
    "ingestion_raw_document",
)
_BUCKET = "civic-raw-test"


def _drop_managed_cascade(conn) -> None:  # type: ignore[no-untyped-def]
    """Drop this test's managed tables with CASCADE.

    CI shares one database, so another module's fixture may have left a table
    that FKs into ``entities_entity`` (the global directory, doc 07 §3) — or
    ``ingestion_raw_document`` itself may carry such a leftover. CASCADE removes
    only the inbound FK constraints, never another module's table, so the drop
    can't trip on ``DependentObjectsStillExistError``. Reverse order so a child
    goes before its parent.
    """
    for name in reversed(_TABLE_NAMES):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {name} CASCADE")


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    tables: list[Table] = [Base.metadata.tables[name] for name in _TABLE_NAMES]
    engine = create_async_engine(_DSN)
    async with engine.begin() as conn:
        # entities_entity carries a pg_trgm trigram index (ensured by its own
        # before_create hook, but the extension must exist for create_all here).
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(_drop_managed_cascade)
        await conn.run_sync(Base.metadata.create_all, tables=tables, checkfirst=True)
    try:
        async with AsyncSession(engine) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_managed_cascade)
        await engine.dispose()


@pytest.fixture
def storage() -> Iterator[RawDocumentStorage]:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield RawDocumentStorage(client, _BUCKET)


async def test_store_persists_provenance_and_bytes(
    session: AsyncSession, storage: RawDocumentStorage
) -> None:
    content = b"<html><body><h1>RFP</h1></body></html>"
    async with session.begin():
        stored = await services.store_raw_document(
            session,
            storage,
            content=content,
            recipe_id="wa_state_webs",
            recipe_version=3,
            connector="http_static",
            source_url="https://webs.des.wa.gov/rfp-1",
            content_type="text/html",
            http_status=200,
            metadata={"etag": "abc123"},
        )

    # Provenance round-trips on the returned schema.
    assert stored.recipe_id == "wa_state_webs"
    assert stored.recipe_version == 3
    assert stored.connector == "http_static"
    assert stored.source_url == "https://webs.des.wa.gov/rfp-1"
    assert stored.http_status == 200
    assert stored.content_type == "text/html"
    assert stored.bytes_size == len(content)
    assert stored.metadata == {"etag": "abc123"}
    assert stored.deduped is False
    assert stored.blob_key == f"sha256/{stored.content_hash}"

    # The bytes are retrievable from S3 at the content-addressed key.
    assert storage.get_document(stored.blob_key) == content

    # The DB row matches.
    row = (
        await session.execute(select(RawDocument).where(RawDocument.id == stored.id))
    ).scalar_one()
    assert row.content_hash == stored.content_hash
    assert row.blob_key == stored.blob_key
    assert row.connector == "http_static"
    assert row.doc_metadata == {"etag": "abc123"}
    assert row.fetched_at is not None
    await session.rollback()


class _CountingStorage(RawDocumentStorage):
    """A storage that counts its conditional-upload calls, so a test can assert the
    dedupe path issues no S3 write (the bytes are already stored). Subclasses the
    real storage and reuses its bucket/client; only the counter is added."""

    def __init__(self, inner: RawDocumentStorage) -> None:
        super().__init__(inner._client, inner.bucket)
        self.if_absent_calls = 0

    def put_document_if_absent(
        self, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> StoredObject:
        self.if_absent_calls += 1
        return super().put_document_if_absent(data, content_type=content_type)


async def test_store_is_idempotent_on_recipe_and_hash(
    session: AsyncSession, storage: RawDocumentStorage
) -> None:
    counting = _CountingStorage(storage)
    content = b"identical fetched content"
    async with session.begin():
        first = await services.store_raw_document(
            session,
            counting,
            content=content,
            recipe_id="r1",
            connector="http_static",
            source_url="https://example.gov/a",
        )
    async with session.begin():
        second = await services.store_raw_document(
            session,
            counting,
            content=content,
            recipe_id="r1",
            connector="http_static",
            # Even from a different URL, identical content for the same recipe
            # dedupes to the same row (UNIQUE recipe_id, content_hash).
            source_url="https://example.gov/b",
        )

    assert second.deduped is True
    assert second.id == first.id
    assert second.content_hash == first.content_hash
    # The dedupe path short-circuits on the DB row and never touches S3.
    assert counting.if_absent_calls == 1

    # Exactly one row exists for this (recipe_id, content_hash).
    rows = (
        (await session.execute(select(RawDocument).where(RawDocument.recipe_id == "r1")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    await session.rollback()


async def test_same_content_different_recipe_gets_own_row(
    session: AsyncSession, storage: RawDocumentStorage
) -> None:
    content = b"shared content across recipes"
    async with session.begin():
        a = await services.store_raw_document(
            session,
            storage,
            content=content,
            recipe_id="ra",
            connector="http_static",
            source_url="https://example.gov/x",
        )
    async with session.begin():
        b = await services.store_raw_document(
            session,
            storage,
            content=content,
            recipe_id="rb",
            connector="http_static",
            source_url="https://example.gov/x",
        )

    # Same bytes -> same S3 key/hash (deduped in S3) ...
    assert a.content_hash == b.content_hash
    assert a.blob_key == b.blob_key
    # ... but distinct provenance rows, one per recipe (doc 07 UNIQUE).
    assert a.id != b.id
    assert b.deduped is False
    await session.rollback()


async def test_entity_link_round_trips(session: AsyncSession, storage: RawDocumentStorage) -> None:
    async with session.begin():
        entity = Entity(type="school_district", name="Northshore School District", state="WA")
        session.add(entity)
        await session.flush()
        entity_id = entity.id

    async with session.begin():
        stored = await services.store_raw_document(
            session,
            storage,
            content=b"doc for an entity",
            recipe_id="r_entity",
            connector="http_static",
            source_url="https://nsd.org/board",
            entity_id=entity_id,
        )
    assert stored.entity_id == entity_id

    # get_raw_document reads it back by id (the E1 read seam).
    fetched = await services.get_raw_document(session, stored.id)
    assert fetched is not None
    assert fetched.entity_id == entity_id
    assert fetched.blob_key == stored.blob_key
    await session.rollback()


async def test_get_missing_returns_none(session: AsyncSession, storage: RawDocumentStorage) -> None:
    assert await services.get_raw_document(session, uuid.uuid4()) is None
    await session.rollback()
