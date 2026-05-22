# SPDX-License-Identifier: AGPL-3.0-only
"""DB-backed tests for the public signal source citations seam (P2).

Covers :func:`signals.services.get_signal_sources` and its HTTP route
(``GET /api/v1/signals/{id}/sources``) — the public, unauthenticated provenance
the ``/s/{id}`` page cites. A signal's ``raw_document_ids`` reference
``ingestion_raw_document`` rows; the seam resolves them to public-safe source URLs
through ``ingestion.services`` (cross-module via the public surface only, doc 06 §3).

Asserts:
- a signal's raw-document refs resolve to ``SignalSource`` rows (url + recipe + time);
- only public-safe fields are exposed (no blob_key / content_hash / http_status);
- duplicate source URLs (from a fuzzy/exact merge appending the same doc, doc 19 §7.3)
  collapse to one citation;
- a dangling raw-document id is skipped, not fatal (no 500 on a public page);
- a signal with no documents resolves to an empty-but-present citation list;
- an unknown signal id resolves to ``None`` (so the route 404s);
- the HTTP route returns the citations unauthenticated and 404s on a missing signal;
- the public-surface allowlist (P2; doc 13 §4.1, §4.6): a non-public (paid-tier) signal
  type 404s on both ``/public`` and ``/sources``, and ``get_public_signal`` returns a
  narrowed projection (no content_hash / raw_document_ids / confidence / status) for a
  public-type signal.

Run against a live Postgres resolved from ``SIGNALS_TEST_DSN`` /
``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` (JSONB + UUID + pgvector are not faithfully
emulated by SQLite); skips cleanly otherwise.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.main import app as main_app
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.ingestion.models import RawDocument
from civicsignals_api.modules.signals import services
from civicsignals_api.modules.signals.models import Signal

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
pytestmark = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)

# The tables this seam touches: the signal row, the ingestion docs it cites, and the
# entities tables the FKs reference (created so the FK DDL resolves; never populated).
_TABLES = ("signals_signal", "ingestion_raw_document")
_ENTITY_TABLES = ("entities_entity", "entities_geo", "entities_kind")


def _drop_cascade(conn: Connection) -> None:
    for tbl in (*_TABLES, *_ENTITY_TABLES):
        conn.exec_driver_sql(f"DROP TABLE IF EXISTS {tbl} CASCADE")


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN, poolclass=NullPool)
    create_tables = [Base.metadata.tables[name] for name in (*_ENTITY_TABLES, *_TABLES)]
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(_drop_cascade)
        await conn.run_sync(Base.metadata.create_all, tables=create_tables, checkfirst=True)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(_drop_cascade)
        await engine.dispose()


async def _raw_document(
    session: AsyncSession,
    *,
    source_url: str,
    recipe_id: str = "wa_k12_rfps",
    fetched_at: datetime | None = None,
) -> RawDocument:
    doc = RawDocument(
        id=uuid.uuid4(),
        recipe_id=recipe_id,
        recipe_version=1,
        connector="http_static",
        source_url=source_url,
        fetched_at=fetched_at or NOW,
        http_status=200,
        content_hash=uuid.uuid4().hex,
        blob_key=f"sha256/{uuid.uuid4().hex}",
        content_type="text/html",
        bytes_size=1024,
    )
    session.add(doc)
    await session.flush()
    return doc


async def _signal(
    session: AsyncSession,
    *,
    raw_document_ids: list[str],
    recipe_id: str = "wa_k12_rfps",
    signal_type: str = "rfp_posted",
) -> Signal:
    sig = Signal(
        id=uuid.uuid4(),
        entity_id=None,
        entity_name_raw="Northshore School District",
        signal_type=signal_type,
        recipe_id=recipe_id,
        raw_document_ids=raw_document_ids,
        content_hash=uuid.uuid4().hex,
        occurred_at=NOW,
        observed_at=NOW,
        title="RFP: Learning Analytics Platform",
        summary="RFP posted for a learning analytics platform.",
        details={"due_at": "2026-06-01T17:00:00Z"},
        confidence=0.9,
    )
    session.add(sig)
    await session.flush()
    return sig


async def test_resolves_documents_to_public_sources(session: AsyncSession) -> None:
    doc_a = await _raw_document(session, source_url="https://nsd.org/rfps/123")
    doc_b = await _raw_document(
        session, source_url="https://bonfirehub.com/portal/NSD123", recipe_id="wa_bonfire"
    )
    sig = await _signal(session, raw_document_ids=[str(doc_a.id), str(doc_b.id)])
    await session.commit()

    result = await services.get_signal_sources(session, sig.id)
    assert result is not None
    assert result.signal_id == sig.id
    urls = {s.source_url for s in result.sources}
    assert urls == {"https://nsd.org/rfps/123", "https://bonfirehub.com/portal/NSD123"}
    by_url = {s.source_url: s for s in result.sources}
    assert by_url["https://nsd.org/rfps/123"].recipe_id == "wa_k12_rfps"
    assert by_url["https://bonfirehub.com/portal/NSD123"].recipe_id == "wa_bonfire"
    assert by_url["https://nsd.org/rfps/123"].document_id == doc_a.id
    # Public-safe projection only: no S3 key / content hash / http status fields.
    dumped = result.sources[0].model_dump()
    assert set(dumped) == {"document_id", "source_url", "recipe_id", "fetched_at"}


async def test_duplicate_source_urls_collapse(session: AsyncSession) -> None:
    """A merge can append the same doc twice (doc 19 §7.3); cite each source once."""
    doc = await _raw_document(session, source_url="https://nsd.org/rfps/dup")
    sig = await _signal(session, raw_document_ids=[str(doc.id), str(doc.id)])
    await session.commit()

    result = await services.get_signal_sources(session, sig.id)
    assert result is not None
    assert len(result.sources) == 1
    assert result.sources[0].source_url == "https://nsd.org/rfps/dup"


async def test_dangling_document_id_is_skipped(session: AsyncSession) -> None:
    doc = await _raw_document(session, source_url="https://nsd.org/rfps/real")
    missing = str(uuid.uuid4())
    sig = await _signal(session, raw_document_ids=[str(doc.id), missing])
    await session.commit()

    result = await services.get_signal_sources(session, sig.id)
    assert result is not None
    assert len(result.sources) == 1
    assert result.sources[0].source_url == "https://nsd.org/rfps/real"


async def test_signal_with_no_documents_returns_empty_sources(session: AsyncSession) -> None:
    sig = await _signal(session, raw_document_ids=[])
    await session.commit()

    result = await services.get_signal_sources(session, sig.id)
    assert result is not None
    assert result.signal_id == sig.id
    assert result.sources == []


async def test_unknown_signal_returns_none(session: AsyncSession) -> None:
    result = await services.get_signal_sources(session, uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_sources_route_is_public_and_returns_citations(session: AsyncSession) -> None:
    doc = await _raw_document(session, source_url="https://nsd.org/rfps/route")
    sig = await _signal(session, raw_document_ids=[str(doc.id)])
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    main_app.dependency_overrides[get_session] = _override_session
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # No auth header, no X-Workspace-Id — the endpoint is public (doc 07 §3).
            resp = await client.get(f"/api/v1/signals/{sig.id}/sources")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["signal_id"] == str(sig.id)
        assert len(body["sources"]) == 1
        assert body["sources"][0]["source_url"] == "https://nsd.org/rfps/route"
        assert body["sources"][0]["recipe_id"] == "wa_k12_rfps"
        assert "blob_key" not in body["sources"][0]
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_sources_route_404s_on_missing_signal(session: AsyncSession) -> None:
    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    main_app.dependency_overrides[get_session] = _override_session
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/signals/{uuid.uuid4()}/sources")
        assert resp.status_code == 404, resp.text
        assert resp.headers["content-type"].startswith("application/problem+json")
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


# ---------------------------------------------------------------------------
# P2 review fix — public-surface allowlist (doc 13 §4.1, §4.6)
# ---------------------------------------------------------------------------
#
# Only late-stage public types (rfp_posted / news_mention / grant_awarded) are
# publicly indexable; paid-tier types must 404 on the public read paths so the API
# cannot be scraped by id.

_PUBLIC_TYPES = ("rfp_posted", "news_mention", "grant_awarded")
_NON_PUBLIC_TYPES = (
    "rfi_rfq",
    "contract_expiring",
    "contract_awarded",
    "budget_approved",
    "grant_opportunity",
    "leadership_change",
    "board_agenda_item",
    "strategic_plan_published",
    "open_job",
)


async def test_get_public_signal_returns_narrowed_projection(session: AsyncSession) -> None:
    """A public-type signal yields the narrowed projection — no internal fields."""
    sig = await _signal(session, raw_document_ids=[], signal_type="rfp_posted")
    await session.commit()

    result = await services.get_public_signal(session, sig.id)
    assert result is not None
    assert result.id == sig.id
    assert result.signal_type == "rfp_posted"
    assert result.title == "RFP: Learning Analytics Platform"
    assert result.entity_name == "Northshore School District"
    # Public-safe projection only: none of the internal SignalRead fields leak.
    dumped = result.model_dump()
    assert set(dumped) == {
        "id",
        "signal_type",
        "title",
        "summary",
        "entity_name",
        "occurred_at",
        "observed_at",
    }
    for leaked in ("content_hash", "raw_document_ids", "confidence", "status", "details"):
        assert leaked not in dumped


@pytest.mark.parametrize("signal_type", _NON_PUBLIC_TYPES)
async def test_get_public_signal_404s_for_non_public_type(
    session: AsyncSession, signal_type: str
) -> None:
    """A paid-tier type resolves to None so the /public route 404s (not scrapable)."""
    sig = await _signal(session, raw_document_ids=[], signal_type=signal_type)
    await session.commit()
    assert await services.get_public_signal(session, sig.id) is None


@pytest.mark.parametrize("signal_type", _NON_PUBLIC_TYPES)
async def test_get_signal_sources_404s_for_non_public_type(
    session: AsyncSession, signal_type: str
) -> None:
    """A paid-tier signal's sources are withheld too — None so the route 404s."""
    doc = await _raw_document(session, source_url="https://example.gov/internal")
    sig = await _signal(session, raw_document_ids=[str(doc.id)], signal_type=signal_type)
    await session.commit()
    assert await services.get_signal_sources(session, sig.id) is None


@pytest.mark.parametrize("signal_type", _PUBLIC_TYPES)
async def test_public_paths_present_for_public_types(
    session: AsyncSession, signal_type: str
) -> None:
    """Each public-allowlist type resolves on both public read seams."""
    doc = await _raw_document(session, source_url=f"https://example.gov/{signal_type}")
    sig = await _signal(session, raw_document_ids=[str(doc.id)], signal_type=signal_type)
    await session.commit()

    public = await services.get_public_signal(session, sig.id)
    assert public is not None and public.signal_type == signal_type

    sources = await services.get_signal_sources(session, sig.id)
    assert sources is not None
    assert {s.source_url for s in sources.sources} == {f"https://example.gov/{signal_type}"}


@pytest.mark.asyncio
async def test_public_routes_404_for_non_public_type(session: AsyncSession) -> None:
    """End-to-end: /public and /sources both 404 (RFC 7807) for a paid-tier signal."""
    doc = await _raw_document(session, source_url="https://example.gov/board")
    sig = await _signal(session, raw_document_ids=[str(doc.id)], signal_type="board_agenda_item")
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    main_app.dependency_overrides[get_session] = _override_session
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            public_resp = await client.get(f"/api/v1/signals/{sig.id}/public")
            sources_resp = await client.get(f"/api/v1/signals/{sig.id}/sources")
        for resp in (public_resp, sources_resp):
            assert resp.status_code == 404, resp.text
            assert resp.headers["content-type"].startswith("application/problem+json")
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_public_route_returns_projection_for_public_type(session: AsyncSession) -> None:
    """End-to-end: /public returns the narrowed shape for a public-type signal."""
    sig = await _signal(session, raw_document_ids=[], signal_type="grant_awarded")
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    main_app.dependency_overrides[get_session] = _override_session
    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/signals/{sig.id}/public")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == str(sig.id)
        assert body["signal_type"] == "grant_awarded"
        # Internal fields are absent from the public projection.
        for leaked in ("content_hash", "raw_document_ids", "confidence", "status", "details"):
            assert leaked not in body
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        await engine.dispose()
