# SPDX-License-Identifier: AGPL-3.0-only
"""HTTP route tests for the G2 signal detail endpoint (GET /signals/{id}/detail).

Tests:
- Detail returns the global signal + the calling workspace's score / breakdown.
- A signal with no score row for the workspace is still viewable (per-workspace
  fields are null) — the corpus is global (doc 07 §3).
- Source documents are resolved from raw_document_ids (a missing ref is a tombstone).
- Suggested contacts at the signal's entity are surfaced.
- Related signals about the same entity are surfaced (self + merged excluded).
- Workspace isolation: workspace B never sees workspace A's score for a signal.
- An unknown signal id → 404 RFC 7807 problem.
- A soft-deleted (merged) signal → 404.

DB-backed (Postgres) — skips cleanly when ``SIGNALS_TEST_DSN`` /
``DATABASE_DIRECT_URL`` / ``DATABASE_URL`` are unset (the WorkspaceScore +
ingestion tables use Numeric + JSONB + TEXT[], which SQLite cannot replicate
faithfully). Mirrors the G1 feed-route test harness.
"""

from __future__ import annotations

import os
import types
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.ids import uuid7
from civicsignals_api.main import app as main_app
from civicsignals_api.modules.accounts.models import Membership, MembershipRole, User, Workspace
from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace
from civicsignals_api.modules.contacts.models import Contact
from civicsignals_api.modules.entities import models as _entities_models  # noqa: F401
from civicsignals_api.modules.entities.models import Entity
from civicsignals_api.modules.ingestion.models import RawDocument
from civicsignals_api.modules.signals.models import SIGNAL_STATUS_MERGED, Signal
from civicsignals_api.modules.signals.workspace_score_model import WorkspaceScore

_DSN = (
    os.environ.get("SIGNALS_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)
_db_skip = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

NOW = datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    assert _DSN is not None
    engine = create_async_engine(_DSN, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# ---------------------------------------------------------------------------
# DB row builders
# ---------------------------------------------------------------------------


async def _workspace(session: AsyncSession, *, email: str) -> uuid.UUID:
    user = User(id=uuid.uuid4(), email=email, password_hash="x", name="T")
    from civicsignals_api.modules.accounts.models import Organization

    org = Organization(id=uuid.uuid4(), name="Org")
    session.add_all([user, org])
    await session.flush()
    ws = Workspace(
        id=uuid.uuid4(),
        organization_id=org.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:12]}",
        owner_id=user.id,
    )
    session.add(ws)
    await session.flush()
    return ws.id


async def _entity(session: AsyncSession, *, name: str = "Northshore SD") -> Entity:
    ent = Entity(id=uuid.uuid4(), type="school_district", name=name, country="US", state="WA")
    session.add(ent)
    await session.flush()
    return ent


async def _signal(
    session: AsyncSession,
    *,
    signal_type: str = "rfp_posted",
    title: str = "RFP: software",
    summary: str = "RFP for software.",
    entity: Entity | None = None,
    entity_name_raw: str | None = None,
    raw_document_ids: list[str] | None = None,
    details: dict[str, object] | None = None,
    status: str = "new",
    observed_at: datetime | None = None,
) -> Signal:
    sig = Signal(
        id=uuid.uuid4(),
        entity_id=entity.id if entity is not None else None,
        entity_name_raw=entity_name_raw,
        signal_type=signal_type,
        recipe_id="r",
        raw_document_ids=raw_document_ids or [],
        content_hash=uuid.uuid4().hex,
        observed_at=observed_at or NOW,
        title=title,
        summary=summary,
        details=details or {},
        confidence=0.9,
        status=status,
    )
    session.add(sig)
    await session.flush()
    return sig


async def _score(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    signal_id: uuid.UUID,
    score: float = 70.0,
    status: str = "new",
    breakdown: dict[str, object] | None = None,
    matched_keywords: list[str] | None = None,
) -> WorkspaceScore:
    row = WorkspaceScore(
        id=uuid7(),
        workspace_id=workspace_id,
        signal_id=signal_id,
        score=score,
        score_breakdown=breakdown or {},
        status=status,
        matched_keywords=matched_keywords or [],
    )
    session.add(row)
    await session.flush()
    return row


async def _raw_document(session: AsyncSession, *, url: str = "https://x.gov/rfp") -> RawDocument:
    doc = RawDocument(
        id=uuid.uuid4(),
        recipe_id="k12_rfp_v3",
        connector="generic_html",
        source_url=url,
        content_hash=uuid.uuid4().hex,
        blob_key=f"raw/{uuid.uuid4().hex}",
        content_type="text/html",
        fetched_at=NOW,
    )
    session.add(doc)
    await session.flush()
    return doc


async def _contact(session: AsyncSession, *, entity_id: uuid.UUID, name: str) -> Contact:
    c = Contact(
        id=uuid7(),
        entity_id=entity_id,
        name=name,
        title="Procurement Director",
        canonical_email=f"{name.lower().replace(' ', '.')}@x.gov",
        verified=True,
    )
    session.add(c)
    await session.flush()
    return c


# ---------------------------------------------------------------------------
# HTTP client helpers
# ---------------------------------------------------------------------------


def _make_ctx(ws_id: uuid.UUID) -> WorkspaceContext:
    user_ns = types.SimpleNamespace(id=uuid.uuid4(), email="t@example.com")
    ws_ns = types.SimpleNamespace(id=ws_id)
    mem_ns = types.SimpleNamespace(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_ns.id,
        role=MembershipRole.MEMBER,
    )
    return WorkspaceContext(
        user=cast(User, user_ns),
        workspace=cast(Workspace, ws_ns),
        membership=cast(Membership, mem_ns),
    )


def _client(ws_id: uuid.UUID) -> tuple[httpx.AsyncClient, object]:
    """Build an ASGI client with overridden session + workspace context."""
    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx(ws_id)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx
    transport = httpx.ASGITransport(app=main_app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return client, engine


def _teardown(engine: object) -> None:
    main_app.dependency_overrides.pop(get_session, None)
    main_app.dependency_overrides.pop(require_workspace, None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_db_skip
@pytest.mark.asyncio
async def test_detail_returns_signal_plus_workspace_score(session: AsyncSession) -> None:
    """The detail returns the global signal + the calling workspace's score row."""
    ws = await _workspace(session, email="detail-score@example.com")
    ent = await _entity(session)
    sig = await _signal(session, title="RFP A", entity=ent, details={"rfp_number": "R-1"})
    await _score(
        session,
        workspace_id=ws,
        signal_id=sig.id,
        score=88.0,
        breakdown={"keywords": 12.0, "bullets": ["matched WA"]},
        matched_keywords=["curriculum"],
    )
    await session.commit()

    client, engine = _client(ws)
    try:
        async with client:
            resp = await client.get(
                f"/api/v1/signals/{sig.id}/detail", headers={"X-Workspace-Id": str(ws)}
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["signal"]["id"] == str(sig.id)
        assert body["signal"]["title"] == "RFP A"
        assert body["score"] == 88.0
        assert body["status"] == "new"
        assert body["score_breakdown"]["keywords"] == 12.0
        assert body["matched_keywords"] == ["curriculum"]
        assert body["entity_name"] == "Northshore SD"
        assert body["extracted_fields"]["rfp_number"] == "R-1"
    finally:
        _teardown(engine)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_detail_signal_without_score_row_is_viewable(session: AsyncSession) -> None:
    """A signal that did not score into the workspace is still viewable (null score)."""
    ws = await _workspace(session, email="detail-noscore@example.com")
    sig = await _signal(session, title="Unscored", entity_name_raw="Some Org")
    await session.commit()

    client, engine = _client(ws)
    try:
        async with client:
            resp = await client.get(
                f"/api/v1/signals/{sig.id}/detail", headers={"X-Workspace-Id": str(ws)}
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["signal"]["id"] == str(sig.id)
        assert body["score"] is None
        assert body["status"] is None
        assert body["score_breakdown"] is None
        assert body["matched_keywords"] == []
        assert body["entity_name"] == "Some Org"
    finally:
        _teardown(engine)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_detail_source_documents_resolved_and_tombstoned(session: AsyncSession) -> None:
    """Source docs resolve from raw_document_ids; a missing ref is a tombstone."""
    ws = await _workspace(session, email="detail-docs@example.com")
    doc = await _raw_document(session, url="https://city.gov/rfp/123")
    missing_id = uuid.uuid4()
    sig = await _signal(
        session, title="Doc-backed", raw_document_ids=[str(doc.id), str(missing_id)]
    )
    await session.commit()

    client, engine = _client(ws)
    try:
        async with client:
            resp = await client.get(
                f"/api/v1/signals/{sig.id}/detail", headers={"X-Workspace-Id": str(ws)}
            )
        assert resp.status_code == 200, resp.text
        docs = resp.json()["source_documents"]
        assert len(docs) == 2
        by_id = {d["raw_document_id"]: d for d in docs}
        resolved = by_id[str(doc.id)]
        assert resolved["source_url"] == "https://city.gov/rfp/123"
        assert resolved["missing"] is False
        assert by_id[str(missing_id)]["missing"] is True
    finally:
        _teardown(engine)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_detail_suggested_contacts_for_entity(session: AsyncSession) -> None:
    """Suggested contacts at the signal's entity are surfaced."""
    ws = await _workspace(session, email="detail-contacts@example.com")
    ent = await _entity(session)
    sig = await _signal(session, entity=ent)
    await _contact(session, entity_id=ent.id, name="Jane Doe")
    await _contact(session, entity_id=ent.id, name="John Roe")
    await session.commit()

    client, engine = _client(ws)
    try:
        async with client:
            resp = await client.get(
                f"/api/v1/signals/{sig.id}/detail", headers={"X-Workspace-Id": str(ws)}
            )
        assert resp.status_code == 200, resp.text
        contacts = resp.json()["suggested_contacts"]
        names = {c["name"] for c in contacts}
        assert names == {"Jane Doe", "John Roe"}
        assert all(c["title"] == "Procurement Director" for c in contacts)
    finally:
        _teardown(engine)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_detail_related_signals_same_entity(session: AsyncSession) -> None:
    """Related signals about the same entity are surfaced (self + merged excluded)."""
    ws = await _workspace(session, email="detail-related@example.com")
    ent = await _entity(session)
    sig = await _signal(session, title="Primary", entity=ent)
    rel = await _signal(session, title="Related", entity=ent)
    await _signal(session, title="Merged", entity=ent, status=SIGNAL_STATUS_MERGED)
    # A signal for a *different* entity must not be related.
    other_ent = await _entity(session, name="Other SD")
    await _signal(session, title="Other entity", entity=other_ent)
    await session.commit()

    client, engine = _client(ws)
    try:
        async with client:
            resp = await client.get(
                f"/api/v1/signals/{sig.id}/detail", headers={"X-Workspace-Id": str(ws)}
            )
        assert resp.status_code == 200, resp.text
        related = resp.json()["related_signals"]
        titles = {r["signal"]["title"] for r in related}
        assert titles == {"Related"}
        assert str(rel.id) in {r["signal"]["id"] for r in related}
    finally:
        _teardown(engine)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_detail_workspace_isolation(session: AsyncSession) -> None:
    """Workspace B never sees workspace A's score for the same global signal."""
    ws_a = await _workspace(session, email="detail-iso-a@example.com")
    ws_b = await _workspace(session, email="detail-iso-b@example.com")
    sig = await _signal(session, title="Shared")
    await _score(session, workspace_id=ws_a, signal_id=sig.id, score=80.0)
    await session.commit()

    # Workspace B sees the signal (global) but no score row.
    client, engine = _client(ws_b)
    try:
        async with client:
            resp = await client.get(
                f"/api/v1/signals/{sig.id}/detail", headers={"X-Workspace-Id": str(ws_b)}
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["signal"]["id"] == str(sig.id)
        assert body["score"] is None
    finally:
        _teardown(engine)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_detail_unknown_signal_returns_404(session: AsyncSession) -> None:
    """An unknown signal id → 404 RFC 7807 problem."""
    ws = await _workspace(session, email="detail-404@example.com")
    await session.commit()

    client, engine = _client(ws)
    try:
        async with client:
            resp = await client.get(
                f"/api/v1/signals/{uuid.uuid4()}/detail", headers={"X-Workspace-Id": str(ws)}
            )
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == 404
        assert "not found" in body["title"].lower()
    finally:
        _teardown(engine)
        await engine.dispose()  # type: ignore[attr-defined]


@_db_skip
@pytest.mark.asyncio
async def test_detail_merged_signal_returns_404(session: AsyncSession) -> None:
    """A soft-deleted (merged) signal is not directly viewable → 404."""
    ws = await _workspace(session, email="detail-merged@example.com")
    sig = await _signal(session, title="Merged away", status=SIGNAL_STATUS_MERGED)
    await session.commit()

    client, engine = _client(ws)
    try:
        async with client:
            resp = await client.get(
                f"/api/v1/signals/{sig.id}/detail", headers={"X-Workspace-Id": str(ws)}
            )
        assert resp.status_code == 404
    finally:
        _teardown(engine)
        await engine.dispose()  # type: ignore[attr-defined]
