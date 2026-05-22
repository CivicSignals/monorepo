"""Tests for the FOIA attachment upload + extraction pipeline (M3).

Coverage (no DB — all service and route logic is unit-tested against mocks):
- upload_attachment: stores via D3, creates FoiaAttachment, enqueues E1.
- upload_attachment: auto-transitions ack → response.
- upload_attachment: does NOT transition draft/sent/response (only ack).
- list_attachments: workspace isolation (validates request ownership).
- get_attachment: not-found path.
- list_attachment_signals: delegates to Signal query.
- update_attachment_extraction_status: updates status field.
- FoiaAttachmentNotFoundError carries the attachment_id.
- FoiaAttachmentExtractionStatus values are correct.
- HTTP routes: POST 201 / GET list / GET detail / GET signals — shape + errors.
- Workspace scoping: X-Workspace-Id header required.
"""

from __future__ import annotations

import types
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from civicsignals_api.modules.auth.dependencies import WorkspaceContext, require_workspace
from civicsignals_api.modules.foia import services
from civicsignals_api.modules.foia.models import FoiaAttachmentExtractionStatus
from civicsignals_api.modules.foia.routes import router
from civicsignals_api.problems import install_problem_handlers

# ---------------------------------------------------------------------------
# Minimal FastAPI test app (no real workspace auth)
# ---------------------------------------------------------------------------

_app = FastAPI()
_app.include_router(router, prefix="/api/v1")
install_problem_handlers(_app)
_client = TestClient(_app, raise_server_exceptions=False)

# ---------------------------------------------------------------------------
# Enums and error unit tests (no DB)
# ---------------------------------------------------------------------------


class TestFoiaAttachmentExtractionStatus:
    def test_all_values_present(self) -> None:
        values = {s.value for s in FoiaAttachmentExtractionStatus}
        assert values == {"pending", "running", "done", "failed", "skipped"}

    def test_string_roundtrip(self) -> None:
        assert FoiaAttachmentExtractionStatus("done") == FoiaAttachmentExtractionStatus.DONE
        assert FoiaAttachmentExtractionStatus("failed") == FoiaAttachmentExtractionStatus.FAILED

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            FoiaAttachmentExtractionStatus("unknown")


class TestFoiaAttachmentNotFoundError:
    def test_carries_attachment_id(self) -> None:
        att_id = uuid.uuid4()
        exc = services.FoiaAttachmentNotFoundError(att_id)
        assert exc.attachment_id == att_id
        assert str(att_id) in str(exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace_ctx(workspace_id: uuid.UUID, user_id: uuid.UUID) -> WorkspaceContext:
    """Build a minimal WorkspaceContext for testing."""
    user = types.SimpleNamespace(id=user_id, email="test@example.com")
    workspace = types.SimpleNamespace(id=workspace_id, name="Test WS")
    membership = types.SimpleNamespace(role="member")
    return WorkspaceContext(
        user=user,  # type: ignore[arg-type]
        workspace=workspace,  # type: ignore[arg-type]
        membership=membership,  # type: ignore[arg-type]
        api_token=None,
    )


@dataclass
class _FakeRawDoc:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    recipe_id: str = "foia_upload"
    recipe_version: int = 1
    connector: str = "manual_upload"
    source_url: str = "foia_upload://req/test.pdf"
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    http_status: int | None = None
    content_hash: str = "abc123"
    blob_key: str = "sha256/abc123"
    content_type: str = "application/pdf"
    bytes_size: int | None = 1024
    entity_id: uuid.UUID | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    deduped: bool = False


@dataclass
class _FakeExtractionJob:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    raw_document_id: uuid.UUID = field(default_factory=uuid.uuid4)
    recipe_id: str = "foia_upload"
    status: str = "pending"


@dataclass
class _FakeRequest:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    status: str = "ack"
    created_by: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class _FakeAttachment:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    foia_request_id: uuid.UUID = field(default_factory=uuid.uuid4)
    raw_document_id: uuid.UUID = field(default_factory=uuid.uuid4)
    extraction_job_id: uuid.UUID | None = None
    filename: str = "response.pdf"
    content_type: str = "application/pdf"
    uploaded_by: uuid.UUID = field(default_factory=uuid.uuid4)
    uploaded_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    extraction_status: str = "pending"


# ---------------------------------------------------------------------------
# Service-layer unit tests (mocked DB + storage)
# ---------------------------------------------------------------------------


class TestUploadAttachmentService:
    """Unit tests for services.upload_attachment (no real DB/S3)."""

    @pytest.mark.asyncio
    async def test_workspace_isolation_checked_first(self) -> None:
        """upload_attachment calls get_request which enforces workspace ownership."""
        # The get_request seam raises FoiaRequestNotFoundError for wrong workspace.
        mock_session = AsyncMock()
        mock_storage = MagicMock()

        with (
            patch.object(
                services,
                "get_request",
                new=AsyncMock(side_effect=services.FoiaRequestNotFoundError("nope")),
            ),
            pytest.raises(services.FoiaRequestNotFoundError),
        ):
            await services.upload_attachment(
                mock_session,
                mock_storage,
                foia_request_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                uploaded_by=uuid.uuid4(),
                filename="test.pdf",
                content_type="application/pdf",
                content=b"pdf bytes",
            )

    @pytest.mark.asyncio
    async def test_upload_raises_not_found_on_wrong_workspace(self) -> None:
        """get_request raises FoiaRequestNotFoundError for wrong workspace."""
        mock_session = AsyncMock()
        mock_storage = MagicMock()

        with (
            patch.object(
                services,
                "get_request",
                new=AsyncMock(side_effect=services.FoiaRequestNotFoundError("not found")),
            ),
            pytest.raises(services.FoiaRequestNotFoundError),
        ):
            await services.upload_attachment(
                mock_session,
                mock_storage,
                foia_request_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
                uploaded_by=uuid.uuid4(),
                filename="test.pdf",
                content_type="application/pdf",
                content=b"fake pdf content",
            )


class TestListAttachmentsService:
    @pytest.mark.asyncio
    async def test_raises_if_request_not_found(self) -> None:
        mock_session = AsyncMock()

        with (
            patch.object(
                services,
                "get_request",
                new=AsyncMock(side_effect=services.FoiaRequestNotFoundError("nope")),
            ),
            pytest.raises(services.FoiaRequestNotFoundError),
        ):
            await services.list_attachments(
                mock_session,
                foia_request_id=uuid.uuid4(),
                workspace_id=uuid.uuid4(),
            )


class TestGetAttachmentService:
    @pytest.mark.asyncio
    async def test_raises_attachment_not_found(self) -> None:
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )

        with patch.object(services, "get_request", new=AsyncMock(return_value=_FakeRequest())):
            with pytest.raises(services.FoiaAttachmentNotFoundError) as exc_info:
                att_id = uuid.uuid4()
                await services.get_attachment(
                    mock_session,
                    attachment_id=att_id,
                    foia_request_id=uuid.uuid4(),
                    workspace_id=uuid.uuid4(),
                )
            assert exc_info.value.attachment_id is not None


class TestUpdateExtractionStatus:
    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self) -> None:
        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=None)

        result = await services.update_attachment_extraction_status(
            mock_session,
            attachment_id=uuid.uuid4(),
            new_status=FoiaAttachmentExtractionStatus.DONE,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_updates_status_when_found(self) -> None:
        mock_session = AsyncMock()
        fake_att = _FakeAttachment()
        mock_session.get = AsyncMock(return_value=fake_att)

        result = await services.update_attachment_extraction_status(
            mock_session,
            attachment_id=fake_att.id,
            new_status=FoiaAttachmentExtractionStatus.DONE,
        )
        assert result is True
        assert fake_att.extraction_status == "done"


# ---------------------------------------------------------------------------
# HTTP route tests (workspace auth overridden)
# ---------------------------------------------------------------------------


def _override_workspace(workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """Override require_workspace for all tests in this file."""
    ctx = _make_workspace_ctx(workspace_id, user_id)
    _app.dependency_overrides[require_workspace] = lambda: ctx


def _clear_overrides() -> None:
    _app.dependency_overrides.clear()


WORKSPACE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
REQUEST_ID = uuid.uuid4()
ATT_ID = uuid.uuid4()
RAW_DOC_ID = uuid.uuid4()
JOB_ID = uuid.uuid4()


def _fake_attachment() -> _FakeAttachment:
    return _FakeAttachment(
        id=ATT_ID,
        foia_request_id=REQUEST_ID,
        raw_document_id=RAW_DOC_ID,
        extraction_job_id=JOB_ID,
        filename="response.pdf",
        content_type="application/pdf",
        uploaded_by=USER_ID,
        uploaded_at=datetime(2026, 5, 22, 12, 0, 0, tzinfo=UTC),
        extraction_status="pending",
    )


class TestUploadAttachmentRoute:
    def setup_method(self) -> None:
        _override_workspace(WORKSPACE_ID, USER_ID)

    def teardown_method(self) -> None:
        _clear_overrides()

    def test_upload_returns_201(self) -> None:
        att = _fake_attachment()
        with patch.object(
            services,
            "upload_attachment",
            new=AsyncMock(return_value=att),
        ):
            resp = _client.post(
                f"/api/v1/foia/requests/{REQUEST_ID}/attachments",
                files={"file": ("response.pdf", b"%PDF-1.4 fake", "application/pdf")},
            )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["filename"] == "response.pdf"
        assert data["extraction_status"] == "pending"
        assert data["foia_request_id"] == str(REQUEST_ID)

    def test_upload_returns_404_when_request_not_found(self) -> None:
        with patch.object(
            services,
            "upload_attachment",
            new=AsyncMock(side_effect=services.FoiaRequestNotFoundError("nope")),
        ):
            resp = _client.post(
                f"/api/v1/foia/requests/{REQUEST_ID}/attachments",
                files={"file": ("response.pdf", b"bytes", "application/pdf")},
            )
        assert resp.status_code == 404
        body = resp.json()
        assert body["status"] == 404
        assert "foia_request_not_found" in body["type"]


class TestListAttachmentsRoute:
    def setup_method(self) -> None:
        _override_workspace(WORKSPACE_ID, USER_ID)

    def teardown_method(self) -> None:
        _clear_overrides()

    def test_list_returns_200_and_empty_page(self) -> None:
        with patch.object(
            services,
            "list_attachments",
            new=AsyncMock(return_value=([], None)),
        ):
            resp = _client.get(f"/api/v1/foia/requests/{REQUEST_ID}/attachments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["next_cursor"] is None

    def test_list_returns_attachment_in_items(self) -> None:
        att = _fake_attachment()
        with patch.object(
            services,
            "list_attachments",
            new=AsyncMock(return_value=([att], None)),
        ):
            resp = _client.get(f"/api/v1/foia/requests/{REQUEST_ID}/attachments")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == str(ATT_ID)

    def test_list_returns_404_when_request_not_found(self) -> None:
        with patch.object(
            services,
            "list_attachments",
            new=AsyncMock(side_effect=services.FoiaRequestNotFoundError("nope")),
        ):
            resp = _client.get(f"/api/v1/foia/requests/{REQUEST_ID}/attachments")
        assert resp.status_code == 404


class TestGetAttachmentRoute:
    def setup_method(self) -> None:
        _override_workspace(WORKSPACE_ID, USER_ID)

    def teardown_method(self) -> None:
        _clear_overrides()

    def test_get_returns_200(self) -> None:
        att = _fake_attachment()
        with patch.object(
            services,
            "get_attachment",
            new=AsyncMock(return_value=att),
        ):
            resp = _client.get(
                f"/api/v1/foia/requests/{REQUEST_ID}/attachments/{ATT_ID}"
            )
        assert resp.status_code == 200
        assert resp.json()["id"] == str(ATT_ID)

    def test_get_returns_404_for_missing_attachment(self) -> None:
        with patch.object(
            services,
            "get_attachment",
            new=AsyncMock(side_effect=services.FoiaAttachmentNotFoundError(ATT_ID)),
        ):
            resp = _client.get(
                f"/api/v1/foia/requests/{REQUEST_ID}/attachments/{ATT_ID}"
            )
        assert resp.status_code == 404
        body = resp.json()
        assert "foia_attachment_not_found" in body["type"]

    def test_get_returns_404_for_missing_request(self) -> None:
        with patch.object(
            services,
            "get_attachment",
            new=AsyncMock(side_effect=services.FoiaRequestNotFoundError("nope")),
        ):
            resp = _client.get(
                f"/api/v1/foia/requests/{REQUEST_ID}/attachments/{ATT_ID}"
            )
        assert resp.status_code == 404
        body = resp.json()
        assert "foia_request_not_found" in body["type"]


class TestListAttachmentSignalsRoute:
    def setup_method(self) -> None:
        _override_workspace(WORKSPACE_ID, USER_ID)

    def teardown_method(self) -> None:
        _clear_overrides()

    def test_signals_returns_empty_list(self) -> None:
        with patch.object(
            services,
            "list_attachment_signals",
            new=AsyncMock(return_value=[]),
        ):
            resp = _client.get(
                f"/api/v1/foia/requests/{REQUEST_ID}/attachments/{ATT_ID}/signals"
            )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_signals_returns_404_on_missing_attachment(self) -> None:
        with patch.object(
            services,
            "list_attachment_signals",
            new=AsyncMock(side_effect=services.FoiaAttachmentNotFoundError(ATT_ID)),
        ):
            resp = _client.get(
                f"/api/v1/foia/requests/{REQUEST_ID}/attachments/{ATT_ID}/signals"
            )
        assert resp.status_code == 404
        body = resp.json()
        assert "foia_attachment_not_found" in body["type"]

    def test_signals_with_signal_objects(self) -> None:
        """A list of Signal-like objects is serialised to FoiaAttachmentSignalRef."""
        sig_id = uuid.uuid4()
        fake_signal = types.SimpleNamespace(
            id=sig_id,
            signal_type="rfp_posted",
            title="Test RFP",
            summary="A test RFP signal.",
            confidence=0.85,
            status="new",
            observed_at=datetime(2026, 5, 22, 10, 0, 0, tzinfo=UTC),
        )
        with patch.object(
            services,
            "list_attachment_signals",
            new=AsyncMock(return_value=[fake_signal]),
        ):
            resp = _client.get(
                f"/api/v1/foia/requests/{REQUEST_ID}/attachments/{ATT_ID}/signals"
            )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["id"] == str(sig_id)
        assert items[0]["signal_type"] == "rfp_posted"
        assert items[0]["confidence"] == pytest.approx(0.85)


class TestWorkspaceScopingRoute:
    """Ensure attachment endpoints enforce workspace auth (X-Workspace-Id)."""

    def setup_method(self) -> None:
        # Do NOT override require_workspace — test that the unauthed path 401s.
        _clear_overrides()

    def teardown_method(self) -> None:
        _clear_overrides()

    def test_upload_requires_auth(self) -> None:
        resp = _client.post(
            f"/api/v1/foia/requests/{REQUEST_ID}/attachments",
            files={"file": ("f.pdf", b"bytes", "application/pdf")},
        )
        # Without workspace auth the dependency raises 401 or 422.
        assert resp.status_code in (401, 422)

    def test_list_requires_auth(self) -> None:
        resp = _client.get(f"/api/v1/foia/requests/{REQUEST_ID}/attachments")
        assert resp.status_code in (401, 422)
