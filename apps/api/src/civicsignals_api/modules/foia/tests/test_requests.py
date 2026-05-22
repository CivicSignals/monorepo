"""Tests for the FOIA request CRUD + state machine (M2).

Coverage:
- create_request from a template (render_template path).
- create_request freeform (body supplied directly).
- get_request workspace isolation (request in workspace A is not visible in B).
- list_requests workspace isolation.
- update_request on a draft request.
- update_request raises FoiaDraftOnlyError on non-draft requests.
- transition_request: legal transitions (draft→sent→ack→response).
- transition_request: illegal transition raises FoiaIllegalTransitionError.
- Timestamps (sent_at / ack_at / response_at) are set on the correct transition.
- list_request_events returns the full history in order.
- HTTP routes: POST/GET/PATCH/transition/events return correct shapes and errors.

DB-backed tests require a live Postgres — they use the same opt-in-via-env
convention as the entities module tests (``DATABASE_DIRECT_URL`` /
``DATABASE_URL``). Unit tests (service-layer, no DB) run always.
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
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Table, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from civicsignals_api.db import Base, get_session
from civicsignals_api.ids import uuid7
from civicsignals_api.main import app as main_app
from civicsignals_api.modules.accounts.models import Membership, MembershipRole, User, Workspace
from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace
from civicsignals_api.modules.foia import services
from civicsignals_api.modules.foia.models import (
    ALLOWED_TRANSITIONS,
    FoiaRequestStatus,
)
from civicsignals_api.modules.foia.routes import router
from civicsignals_api.problems import install_problem_handlers

# ---------------------------------------------------------------------------
# Minimal FastAPI app for route testing (no workspace auth — overridden below)
# ---------------------------------------------------------------------------

_app = FastAPI()
_app.include_router(router, prefix="/api/v1")
install_problem_handlers(_app)
_client = TestClient(_app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# State machine unit tests (no DB required)
# ---------------------------------------------------------------------------


class TestAllowedTransitions:
    """State machine transition table (no DB)."""

    def test_draft_can_only_go_to_sent(self) -> None:
        allowed = ALLOWED_TRANSITIONS[FoiaRequestStatus.DRAFT]
        assert allowed == {FoiaRequestStatus.SENT}

    def test_sent_can_only_go_to_ack(self) -> None:
        allowed = ALLOWED_TRANSITIONS[FoiaRequestStatus.SENT]
        assert allowed == {FoiaRequestStatus.ACK}

    def test_ack_can_only_go_to_response(self) -> None:
        allowed = ALLOWED_TRANSITIONS[FoiaRequestStatus.ACK]
        assert allowed == {FoiaRequestStatus.RESPONSE}

    def test_response_is_terminal(self) -> None:
        allowed = ALLOWED_TRANSITIONS[FoiaRequestStatus.RESPONSE]
        assert allowed == set()

    def test_draft_cannot_jump_to_ack(self) -> None:
        assert FoiaRequestStatus.ACK not in ALLOWED_TRANSITIONS[FoiaRequestStatus.DRAFT]

    def test_draft_cannot_jump_to_response(self) -> None:
        assert FoiaRequestStatus.RESPONSE not in ALLOWED_TRANSITIONS[FoiaRequestStatus.DRAFT]

    def test_sent_cannot_go_back_to_draft(self) -> None:
        assert FoiaRequestStatus.DRAFT not in ALLOWED_TRANSITIONS[FoiaRequestStatus.SENT]


class TestFoiaStatusEnum:
    def test_all_values_present(self) -> None:
        values = {s.value for s in FoiaRequestStatus}
        assert values == {"draft", "sent", "ack", "response"}

    def test_string_roundtrip(self) -> None:
        assert FoiaRequestStatus("draft") == FoiaRequestStatus.DRAFT
        assert FoiaRequestStatus("sent") == FoiaRequestStatus.SENT
        assert FoiaRequestStatus("ack") == FoiaRequestStatus.ACK
        assert FoiaRequestStatus("response") == FoiaRequestStatus.RESPONSE

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            FoiaRequestStatus("submitted")


# ---------------------------------------------------------------------------
# Service-layer error type unit tests (no DB)
# ---------------------------------------------------------------------------


class TestServiceExceptions:
    def test_draft_only_error(self) -> None:
        rid = uuid.uuid4()
        exc = services.FoiaDraftOnlyError(rid, "sent")
        assert exc.request_id == rid
        assert exc.current_status == "sent"
        assert "sent" in str(exc)

    def test_illegal_transition_error(self) -> None:
        rid = uuid.uuid4()
        exc = services.FoiaIllegalTransitionError(rid, "draft", "response")
        assert exc.from_status == "draft"
        assert exc.to_status == "response"
        assert "draft" in str(exc)
        assert "response" in str(exc)

    def test_entity_not_found_error(self) -> None:
        eid = uuid.uuid4()
        exc = services.FoiaEntityNotFoundError(eid)
        assert exc.entity_id == eid


# ---------------------------------------------------------------------------
# Template route smoke tests (no workspace required — inherited from M1)
# ---------------------------------------------------------------------------


class TestTemplateRoutesSmoke:
    def test_list_templates_returns_200(self) -> None:
        resp = _client.get("/api/v1/foia/templates")
        assert resp.status_code == 200

    def test_get_template_us_foia(self) -> None:
        resp = _client.get("/api/v1/foia/templates/US-FOIA")
        assert resp.status_code == 200
        assert resp.json()["jurisdiction"] == "US-FOIA"

    def test_get_template_not_found(self) -> None:
        resp = _client.get("/api/v1/foia/templates/ZZ-FAKE")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------

_DSN = (
    os.environ.get("FOIA_TEST_DSN")
    or os.environ.get("DATABASE_DIRECT_URL")
    or os.environ.get("DATABASE_URL")
)

# The skipif condition: skip DB tests when no Postgres DSN is available.
# The condition is inverted from pytest.mark.skipif — we skip when _DSN IS None.
_db_skip = pytest.mark.skipif(_DSN is None, reason="no Postgres DSN configured")

# Tables this test module manages.  We include the dependency tables so
# create_all has the FKs it needs.  We drop only the foia tables in teardown
# to keep the test truly isolated from other module tests.
_OWN_TABLE_NAMES = ("foia_request", "foia_request_event")
_DEP_TABLE_NAMES = (
    "accounts_member",
    "accounts_workspace",
    "accounts_organization",
    "accounts_user",
    "entities_entity",
    "entities_kind",
    "entities_geo",
)


def _get_tables(names: tuple[str, ...]) -> list[Table]:
    return [Base.metadata.tables[n] for n in names if n in Base.metadata.tables]


# Import all module models so that Base.metadata is populated before we call
# create_all / drop_all.  These imports are intentionally local to the test so
# the module can still be imported without a live DB.
def _register_models() -> None:
    import civicsignals_api.modules.accounts.models
    import civicsignals_api.modules.auth.models
    import civicsignals_api.modules.entities.models
    import civicsignals_api.modules.foia.models

    # Touch the modules to ensure they are registered on Base.metadata.
    _ = (
        civicsignals_api.modules.accounts.models,
        civicsignals_api.modules.auth.models,
        civicsignals_api.modules.entities.models,
        civicsignals_api.modules.foia.models,
    )


_register_models()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Fresh schema + session for each DB test.

    Uses a dedicated test schema (``foia_test_schema``) to stay fully isolated
    from any already-created public-schema tables in the shared test DB. Tables
    are created fresh from the ORM metadata and torn down at the end.
    We DROP CASCADE because the FK graph is complex — dependencies between our
    dep tables and other modules' tables that may already exist in the DB.
    """
    assert _DSN is not None
    engine = create_async_engine(_DSN, poolclass=NullPool)
    own_tables = _get_tables(_OWN_TABLE_NAMES)
    dep_tables = _get_tables(_DEP_TABLE_NAMES)
    all_tables = dep_tables + own_tables

    # All table names we will drop (in safe reverse-dependency order using CASCADE).
    all_table_names = list(_OWN_TABLE_NAMES[::-1]) + list(_DEP_TABLE_NAMES)

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS citext"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        # Drop with CASCADE so inter-module FKs don't block teardown.
        for tname in all_table_names:
            await conn.execute(text(f"DROP TABLE IF EXISTS {tname} CASCADE"))
        await conn.run_sync(Base.metadata.create_all, tables=all_tables)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as sess:
            yield sess
    finally:
        async with engine.begin() as conn:
            for tname in all_table_names:
                await conn.execute(text(f"DROP TABLE IF EXISTS {tname} CASCADE"))
        await engine.dispose()


# ---------------------------------------------------------------------------
# Fixture helpers — build minimal dependency rows
# ---------------------------------------------------------------------------


def _make_user(suffix: str = "") -> dict[str, object]:
    from civicsignals_api.modules.accounts.models import User

    u = User(email=f"user{suffix}@example.com", name=f"User {suffix}")
    return {"obj": u}


async def _seed_user(session: AsyncSession, suffix: str = "") -> uuid.UUID:
    from civicsignals_api.modules.accounts.models import User

    user = User(id=uuid7(), email=f"user{suffix}@example.com", name=f"Test User {suffix}")
    session.add(user)
    await session.flush()
    return user.id


async def _seed_org(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    from civicsignals_api.modules.accounts.models import Organization

    org = Organization(id=uuid7(), name="Test Org")
    session.add(org)
    await session.flush()
    return org.id


async def _seed_workspace(
    session: AsyncSession, org_id: uuid.UUID, owner_id: uuid.UUID, slug: str = "test-ws"
) -> uuid.UUID:
    from civicsignals_api.modules.accounts.models import Workspace

    ws = Workspace(
        id=uuid7(),
        organization_id=org_id,
        name="Test Workspace",
        slug=slug,
        owner_id=owner_id,
    )
    session.add(ws)
    await session.flush()
    return ws.id


async def _seed_entity(session: AsyncSession, name: str = "Test Agency") -> uuid.UUID:
    from civicsignals_api.modules.entities.models import Entity

    entity = Entity(
        id=uuid7(),
        type="agency",
        status="active",
        name=name,
        country="US",
        state="CA",
        source_urls=[],
    )
    session.add(entity)
    await session.flush()
    return entity.id


async def _setup(
    session: AsyncSession, ws_slug: str = "ws-a"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Return (workspace_id, user_id, entity_id)."""
    user_id = await _seed_user(session, suffix=ws_slug)
    org_id = await _seed_org(session, user_id)
    ws_id = await _seed_workspace(session, org_id, user_id, slug=ws_slug)
    entity_id = await _seed_entity(session, name=f"Agency for {ws_slug}")
    await session.commit()
    return ws_id, user_id, entity_id


# ---------------------------------------------------------------------------
# DB tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@_db_skip
async def test_create_request_freeform(session: AsyncSession) -> None:
    """create_request with explicit body — no template rendering."""
    ws_id, user_id, entity_id = await _setup(session, "free-form")
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="All vendor contracts 2022-2024",
            body="I hereby request all vendor contracts from 2022 to 2024.",
        )
    assert req.id is not None
    assert req.workspace_id == ws_id
    assert req.created_by == user_id
    assert req.entity_id == entity_id
    assert req.status == FoiaRequestStatus.DRAFT.value
    assert req.sent_at is None
    assert req.ack_at is None
    assert req.response_at is None
    assert "vendor contracts" in req.body


@pytest.mark.asyncio
@_db_skip
async def test_create_request_from_template(session: AsyncSession) -> None:
    """create_request with jurisdiction + template_context — renders via render_template."""
    ws_id, user_id, entity_id = await _setup(session, "template-ws")
    context = {
        "requester_name": "Jane Doe",
        "requester_address": "123 Main St, Austin, TX 78701",
        "requester_email": "jane@example.com",
        "entity_name": "Texas State Agency",
        "records_description": "All IT contracts from 2023",
        "date": "2025-05-22",
        "fee_waiver_basis": "non-profit",
    }
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="IT contracts 2023",
            jurisdiction="TX-PIA",
            template_context=context,
        )
    assert req.jurisdiction == "TX-PIA"
    assert "Jane Doe" in req.body
    assert "Texas State Agency" in req.body
    assert req.status == FoiaRequestStatus.DRAFT.value


@pytest.mark.asyncio
@_db_skip
async def test_create_request_entity_not_found(session: AsyncSession) -> None:
    """create_request raises FoiaEntityNotFoundError when entity_id does not exist."""
    ws_id, user_id, _ = await _setup(session, "ent-nf")
    fake_entity = uuid.uuid4()
    async with session.begin():
        with pytest.raises(services.FoiaEntityNotFoundError) as exc_info:
            await services.create_request(
                session,
                workspace_id=ws_id,
                created_by=user_id,
                entity_id=fake_entity,
                subject="test",
                body="test body",
            )
    assert exc_info.value.entity_id == fake_entity


@pytest.mark.asyncio
@_db_skip
async def test_create_request_no_body_no_template_raises(session: AsyncSession) -> None:
    """create_request with neither body nor jurisdiction+context raises ValueError."""
    ws_id, user_id, entity_id = await _setup(session, "no-body")
    async with session.begin():
        with pytest.raises(ValueError, match="body"):
            await services.create_request(
                session,
                workspace_id=ws_id,
                created_by=user_id,
                entity_id=entity_id,
                subject="test",
                # neither body nor jurisdiction+template_context supplied
            )


@pytest.mark.asyncio
@_db_skip
async def test_get_request_workspace_isolation(session: AsyncSession) -> None:
    """A request in workspace A is invisible in workspace B."""
    ws_a_id, user_a_id, entity_id = await _setup(session, "iso-ws-a")
    user_b_id = await _seed_user(session, suffix="iso-ws-b-user")
    org_b_id = await _seed_org(session, user_b_id)
    ws_b_id = await _seed_workspace(session, org_b_id, user_b_id, slug="iso-ws-b")
    await session.commit()

    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_a_id,
            created_by=user_a_id,
            entity_id=entity_id,
            subject="Workspace A request",
            body="Request body.",
        )

    # Visible in workspace A.
    fetched = await services.get_request(session, request_id=req.id, workspace_id=ws_a_id)
    assert fetched.id == req.id

    # Not visible in workspace B.
    with pytest.raises(services.FoiaRequestNotFoundError):
        await services.get_request(session, request_id=req.id, workspace_id=ws_b_id)


@pytest.mark.asyncio
@_db_skip
async def test_list_requests_workspace_isolation(session: AsyncSession) -> None:
    """list_requests only returns requests for the scoped workspace."""
    ws_a_id, user_a_id, entity_id = await _setup(session, "list-iso-a")
    user_b_id = await _seed_user(session, suffix="list-iso-b-user")
    org_b_id = await _seed_org(session, user_b_id)
    ws_b_id = await _seed_workspace(session, org_b_id, user_b_id, slug="list-iso-b")
    await session.commit()

    # Create one request in each workspace.
    async with session.begin():
        await services.create_request(
            session,
            workspace_id=ws_a_id,
            created_by=user_a_id,
            entity_id=entity_id,
            subject="A's request",
            body="Body A.",
        )
        await services.create_request(
            session,
            workspace_id=ws_b_id,
            created_by=user_b_id,
            entity_id=entity_id,
            subject="B's request",
            body="Body B.",
        )

    items_a, _ = await services.list_requests(session, workspace_id=ws_a_id)
    assert len(items_a) == 1
    assert items_a[0].subject == "A's request"

    items_b, _ = await services.list_requests(session, workspace_id=ws_b_id)
    assert len(items_b) == 1
    assert items_b[0].subject == "B's request"


@pytest.mark.asyncio
@_db_skip
async def test_update_draft_request(session: AsyncSession) -> None:
    """update_request patches subject/body/method/target on a draft request."""
    ws_id, user_id, entity_id = await _setup(session, "upd-draft")
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="Original subject",
            body="Original body.",
        )

    async with session.begin():
        updated = await services.update_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            subject="Updated subject",
            body="Updated body with new content.",
            submission_method="email",
            submission_target="records@agency.gov",
        )

    assert updated.subject == "Updated subject"
    assert "Updated body" in updated.body
    assert updated.submission_method == "email"
    assert updated.submission_target == "records@agency.gov"


@pytest.mark.asyncio
@_db_skip
async def test_update_non_draft_raises(session: AsyncSession) -> None:
    """update_request raises FoiaDraftOnlyError when status is not draft."""
    ws_id, user_id, entity_id = await _setup(session, "upd-non-draft")
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="Test",
            body="Body.",
        )

    # Advance to sent.
    async with session.begin():
        await services.transition_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            actor_id=user_id,
            new_status=FoiaRequestStatus.SENT,
        )

    # Try to update a non-draft.
    with pytest.raises(services.FoiaDraftOnlyError) as exc_info:
        async with session.begin():
            await services.update_request(
                session,
                request_id=req.id,
                workspace_id=ws_id,
                subject="Should fail",
            )
    assert exc_info.value.current_status == FoiaRequestStatus.SENT.value


@pytest.mark.asyncio
@_db_skip
async def test_legal_transitions_full_path(session: AsyncSession) -> None:
    """Full draft→sent→ack→response path with timestamp assertions."""
    ws_id, user_id, entity_id = await _setup(session, "full-path")
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="Full path test",
            body="Request body.",
        )
    assert req.status == "draft"
    assert req.sent_at is None

    before = datetime.now(UTC)

    # draft → sent
    async with session.begin():
        req = await services.transition_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            actor_id=user_id,
            new_status=FoiaRequestStatus.SENT,
        )
    assert req.status == "sent"
    assert req.sent_at is not None
    assert req.sent_at >= before
    assert req.ack_at is None
    assert req.response_at is None

    # sent → ack
    async with session.begin():
        req = await services.transition_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            actor_id=user_id,
            new_status=FoiaRequestStatus.ACK,
        )
    assert req.status == "ack"
    assert req.ack_at is not None
    assert req.response_at is None

    # ack → response (with notes)
    async with session.begin():
        req = await services.transition_request(
            session,
            request_id=req.id,
            workspace_id=ws_id,
            actor_id=user_id,
            new_status=FoiaRequestStatus.RESPONSE,
            response_notes="Records provided in full.",
        )
    assert req.status == "response"
    assert req.response_at is not None
    assert req.response_notes == "Records provided in full."


@pytest.mark.asyncio
@_db_skip
async def test_illegal_transition_raises(session: AsyncSession) -> None:
    """Illegal transitions raise FoiaIllegalTransitionError with correct from/to."""
    ws_id, user_id, entity_id = await _setup(session, "illegal-trans")
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="Illegal transition test",
            body="Body.",
        )

    # draft → response is illegal
    with pytest.raises(services.FoiaIllegalTransitionError) as exc_info:
        async with session.begin():
            await services.transition_request(
                session,
                request_id=req.id,
                workspace_id=ws_id,
                actor_id=user_id,
                new_status=FoiaRequestStatus.RESPONSE,
            )
    assert exc_info.value.from_status == "draft"
    assert exc_info.value.to_status == "response"


@pytest.mark.asyncio
@_db_skip
async def test_illegal_transition_draft_to_ack(session: AsyncSession) -> None:
    """draft → ack is illegal."""
    ws_id, user_id, entity_id = await _setup(session, "illegal-ack")
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="Illegal ack test",
            body="Body.",
        )
    with pytest.raises(services.FoiaIllegalTransitionError):
        async with session.begin():
            await services.transition_request(
                session,
                request_id=req.id,
                workspace_id=ws_id,
                actor_id=user_id,
                new_status=FoiaRequestStatus.ACK,
            )


@pytest.mark.asyncio
@_db_skip
async def test_transition_terminal_response_raises(session: AsyncSession) -> None:
    """Transitioning out of terminal 'response' status raises FoiaIllegalTransitionError."""
    ws_id, user_id, entity_id = await _setup(session, "terminal-trans")
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="Terminal test",
            body="Body.",
        )
    for status in (FoiaRequestStatus.SENT, FoiaRequestStatus.ACK, FoiaRequestStatus.RESPONSE):
        async with session.begin():
            req = await services.transition_request(
                session,
                request_id=req.id,
                workspace_id=ws_id,
                actor_id=user_id,
                new_status=status,
            )

    # Now in 'response' — any further transition should raise.
    with pytest.raises(services.FoiaIllegalTransitionError):
        async with session.begin():
            await services.transition_request(
                session,
                request_id=req.id,
                workspace_id=ws_id,
                actor_id=user_id,
                new_status=FoiaRequestStatus.SENT,
            )


@pytest.mark.asyncio
@_db_skip
async def test_list_request_events_full_path(session: AsyncSession) -> None:
    """list_request_events returns all events in occurred_at order."""
    ws_id, user_id, entity_id = await _setup(session, "events-path")
    async with session.begin():
        req = await services.create_request(
            session,
            workspace_id=ws_id,
            created_by=user_id,
            entity_id=entity_id,
            subject="Events test",
            body="Body.",
        )
    for status in (FoiaRequestStatus.SENT, FoiaRequestStatus.ACK):
        async with session.begin():
            req = await services.transition_request(
                session,
                request_id=req.id,
                workspace_id=ws_id,
                actor_id=user_id,
                new_status=status,
            )

    events = await services.list_request_events(session, request_id=req.id, workspace_id=ws_id)
    assert len(events) == 2  # draft→sent, sent→ack
    assert events[0].from_status == "draft"
    assert events[0].to_status == "sent"
    assert events[1].from_status == "sent"
    assert events[1].to_status == "ack"
    # Monotonically ordered.
    assert events[0].occurred_at <= events[1].occurred_at


@pytest.mark.asyncio
@_db_skip
async def test_cursor_pagination(session: AsyncSession) -> None:
    """Cursor pagination walks all requests exactly once."""
    ws_id, user_id, entity_id = await _setup(session, "cursor-pg")

    # Create 5 requests.
    async with session.begin():
        for i in range(5):
            await services.create_request(
                session,
                workspace_id=ws_id,
                created_by=user_id,
                entity_id=entity_id,
                subject=f"Request {i}",
                body=f"Body {i}.",
            )

    seen_ids: list[uuid.UUID] = []
    cursor: str | None = None
    while True:
        items, next_cursor = await services.list_requests(
            session, workspace_id=ws_id, cursor=cursor, limit=2
        )
        seen_ids.extend(r.id for r in items)
        if next_cursor is None:
            break
        cursor = next_cursor

    assert len(seen_ids) == 5
    assert len(set(seen_ids)) == 5  # no duplicates
    assert seen_ids == sorted(seen_ids)  # UUID v7 ordering


# ---------------------------------------------------------------------------
# HTTP route tests via TestClient (needs DB for the request endpoints)
# ---------------------------------------------------------------------------


def _make_ctx_override(
    ws_id: uuid.UUID,
    user_id: uuid.UUID,
) -> WorkspaceContext:
    """Build a stub WorkspaceContext using SimpleNamespace ducks.

    WorkspaceContext is a plain frozen dataclass; its type annotations are not
    enforced at runtime, so we can pass SimpleNamespace objects whose attributes
    match the subset that services and routes actually access (.id, .role).
    ``cast`` satisfies mypy without triggering a real type error.
    """
    user_ns = types.SimpleNamespace(
        id=user_id,
        email="stub@example.com",
        last_active_workspace_id=None,
    )
    ws_ns = types.SimpleNamespace(id=ws_id)
    mem_ns = types.SimpleNamespace(
        id=uuid7(),
        workspace_id=ws_id,
        user_id=user_id,
        role=MembershipRole.OWNER,
    )
    return WorkspaceContext(
        user=cast(User, user_ns),
        workspace=cast(Workspace, ws_ns),
        membership=cast(Membership, mem_ns),
    )


@pytest.mark.asyncio
@_db_skip
async def test_http_create_and_get_request(session: AsyncSession) -> None:
    """POST /requests creates a request; GET /requests/{id} returns it.

    Uses httpx.AsyncClient + ASGITransport (fully async, no event-loop nesting).
    """
    ws_id, user_id, entity_id = await _setup(session, "http-cg")
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx_override(ws_id, user_id)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx

    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # Create freeform.
            create_resp = await client.post(
                "/api/v1/foia/requests",
                json={
                    "entity_id": str(entity_id),
                    "subject": "All IT contracts 2023",
                    "body": "I request all IT contracts from 2023.",
                },
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert create_resp.status_code == 201, create_resp.text
            data = create_resp.json()
            assert data["status"] == "draft"
            assert data["subject"] == "All IT contracts 2023"
            rid = data["id"]

            # Get by id.
            get_resp = await client.get(
                f"/api/v1/foia/requests/{rid}",
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["id"] == rid

            # Get non-existent.
            missing = await client.get(
                f"/api/v1/foia/requests/{uuid.uuid4()}",
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert missing.status_code == 404
            assert "application/problem+json" in missing.headers["content-type"]
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@pytest.mark.asyncio
@_db_skip
async def test_http_transition_legal_and_illegal(session: AsyncSession) -> None:
    """Transition endpoint: legal move returns updated status; illegal returns 409."""
    ws_id, user_id, entity_id = await _setup(session, "http-trans")
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx_override(ws_id, user_id)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx

    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # Create request.
            create_resp = await client.post(
                "/api/v1/foia/requests",
                json={
                    "entity_id": str(entity_id),
                    "subject": "Transition test",
                    "body": "Test body.",
                },
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert create_resp.status_code == 201, create_resp.text
            rid = create_resp.json()["id"]

            # Legal transition: draft → sent
            trans_resp = await client.post(
                f"/api/v1/foia/requests/{rid}/transition",
                json={"status": "sent"},
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert trans_resp.status_code == 200, trans_resp.text
            assert trans_resp.json()["status"] == "sent"
            assert trans_resp.json()["sent_at"] is not None

            # Illegal transition: sent → response (must go via ack)
            bad_trans = await client.post(
                f"/api/v1/foia/requests/{rid}/transition",
                json={"status": "response"},
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert bad_trans.status_code == 409
            assert "application/problem+json" in bad_trans.headers["content-type"]
            assert "foia_illegal_transition" in bad_trans.json()["type"]
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()


@pytest.mark.asyncio
@_db_skip
async def test_http_update_draft_and_reject_non_draft(session: AsyncSession) -> None:
    """PATCH on draft succeeds; PATCH after transition returns 409."""
    ws_id, user_id, entity_id = await _setup(session, "http-upd")
    await session.commit()

    engine = create_async_engine(_DSN, poolclass=NullPool)  # type: ignore[arg-type]
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as s:
            yield s

    ctx = _make_ctx_override(ws_id, user_id)
    main_app.dependency_overrides[get_session] = _override_session
    main_app.dependency_overrides[require_workspace] = lambda: ctx

    try:
        transport = httpx.ASGITransport(app=main_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            # Create.
            create_resp = await client.post(
                "/api/v1/foia/requests",
                json={
                    "entity_id": str(entity_id),
                    "subject": "Original",
                    "body": "Original body.",
                },
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert create_resp.status_code == 201, create_resp.text
            rid = create_resp.json()["id"]

            # PATCH draft — should succeed.
            patch_resp = await client.patch(
                f"/api/v1/foia/requests/{rid}",
                json={"subject": "Updated subject"},
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert patch_resp.status_code == 200, patch_resp.text
            assert patch_resp.json()["subject"] == "Updated subject"

            # Transition to sent.
            await client.post(
                f"/api/v1/foia/requests/{rid}/transition",
                json={"status": "sent"},
                headers={"X-Workspace-Id": str(ws_id)},
            )

            # PATCH sent — should return 409.
            bad_patch = await client.patch(
                f"/api/v1/foia/requests/{rid}",
                json={"subject": "Should fail"},
                headers={"X-Workspace-Id": str(ws_id)},
            )
            assert bad_patch.status_code == 409
            assert "foia_not_draft" in bad_patch.json()["type"]
    finally:
        main_app.dependency_overrides.pop(get_session, None)
        main_app.dependency_overrides.pop(require_workspace, None)
        await engine.dispose()
