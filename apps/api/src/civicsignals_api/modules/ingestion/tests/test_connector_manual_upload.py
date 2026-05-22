"""Tests for the ``manual_upload`` connector (doc 18 §1 cat. H, §5 wave 5; D10).

Covers:
- Connector registration and config parsing (wave-5 registry test).
- ``discover`` is a no-op; ``build_fetcher`` returns the no-op fetcher.
- ``_NoOpFetcher.fetch`` raises ``ConnectorError`` (never called in production).
- CSV validation: size, columns, empty body.
- CSV import: valid rows enqueued, malformed rows skipped + reported.
- ``submit_raw_document``: routes a stored doc into the extraction pipeline.
- Workspace isolation: recipe_id carries the workspace_id so rows from different
  workspaces don't share provenance.
- Schema test: ``manual_upload`` validates against the recipe JSON Schema.
"""

from __future__ import annotations

import io
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from civicsignals_api.modules.ingestion.connectors import (
    ManualUploadConfig,
    ManualUploadConnector,
    connector_for,
    registered_names,
)
from civicsignals_api.modules.ingestion.connectors.base import ConnectorError
from civicsignals_api.modules.ingestion.manual_upload_services import (
    MAX_CSV_BYTES,
    REQUIRED_CSV_COLUMNS,
    CsvMissingColumnsError,
    CsvNoRowsError,
    CsvTooLargeError,
    RawDocumentNotFoundError,
    _row_to_csv_bytes,
    import_csv,
    submit_raw_document,
    validate_csv,
)
from civicsignals_api.modules.recipes import services as recipes_services
from civicsignals_api.modules.recipes.services import Recipe

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_recipe(**overrides: object) -> Recipe:
    data: dict[str, object] = {
        "recipe_id": "manual-upload-test",
        "connector": "manual_upload",
        "version": 1,
        "entity": {"name": "Test District"},
        **overrides,
    }
    return recipes_services.parse_recipe(data)


def _make_csv(rows: list[dict[str, str]], extra_header: list[str] | None = None) -> bytes:
    """Build a minimal CSV with the required + any extra columns."""
    import csv

    fieldnames = list(REQUIRED_CSV_COLUMNS) + (extra_header or [])
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({**dict.fromkeys(fieldnames, ""), **row})
    return buf.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# Connector registration
# ---------------------------------------------------------------------------


def test_wave5_manual_upload_registered() -> None:
    """manual_upload must be registered as a wave-5 connector (D10)."""
    assert "manual_upload" in registered_names()


def test_connector_for_manual_upload_instantiates() -> None:
    recipe = _make_recipe()
    conn = connector_for(recipe)
    assert isinstance(conn, ManualUploadConnector)


def test_manual_upload_config_defaults() -> None:
    recipe = _make_recipe()
    conn = ManualUploadConnector(recipe)
    assert isinstance(conn.config, ManualUploadConfig)
    assert conn.config.prefilter == "assume_relevant"


def test_manual_upload_config_override() -> None:
    recipe = _make_recipe(
        connector_config={"manual_upload": {"prefilter": "classifier"}}
    )
    conn = ManualUploadConnector(recipe)
    assert isinstance(conn.config, ManualUploadConfig)
    assert conn.config.prefilter == "classifier"


# ---------------------------------------------------------------------------
# Connector lifecycle: discover + fetch
# ---------------------------------------------------------------------------


def test_discover_returns_empty_list() -> None:
    """discover() is a no-op for manual uploads (bytes are pushed, not polled)."""
    recipe = _make_recipe()
    conn = ManualUploadConnector(recipe)
    assert conn.discover(["https://example.gov/some-url"]) == []
    assert conn.discover([]) == []


def test_build_fetcher_returns_noop_fetcher() -> None:
    """build_fetcher() must return a no-op fetcher."""
    recipe = _make_recipe()
    conn = ManualUploadConnector(recipe)
    fetcher = conn.build_fetcher()
    assert fetcher is not None


def test_noop_fetcher_fetch_raises_connector_error() -> None:
    """_NoOpFetcher.fetch() must raise ConnectorError — it is never called in production."""
    recipe = _make_recipe()
    conn = ManualUploadConnector(recipe)
    fetcher = conn.build_fetcher()
    with pytest.raises(ConnectorError, match="never be called"):
        fetcher.fetch("https://example.gov", user_agent="civicsignals-bot", max_redirects=5)


def test_noop_fetcher_robots_txt_returns_none() -> None:
    """robots_txt() must return None (no URL to check)."""
    recipe = _make_recipe()
    conn = ManualUploadConnector(recipe)
    fetcher = conn.build_fetcher()
    result = fetcher.robots_txt("https://example.gov", user_agent="civicsignals-bot")
    assert result is None


# ---------------------------------------------------------------------------
# CSV validation
# ---------------------------------------------------------------------------


def test_validate_csv_valid_rows() -> None:
    content = _make_csv([{"title": "Bid #001"}, {"title": "Bid #002"}])
    header, rows = validate_csv(content, filename="test.csv")
    assert "title" in header
    assert len(rows) == 2
    assert rows[0]["title"] == "Bid #001"


def test_validate_csv_too_large() -> None:
    oversized = b"x" * (MAX_CSV_BYTES + 1)
    with pytest.raises(CsvTooLargeError) as exc_info:
        validate_csv(oversized, filename="big.csv")
    assert exc_info.value.size == len(oversized)


def test_validate_csv_missing_required_column() -> None:
    # Build a CSV without the 'title' column.
    import csv

    buf = io.StringIO()
    csv.writer(buf).writerows([["description"], ["some description"]])
    content = buf.getvalue().encode("utf-8")
    with pytest.raises(CsvMissingColumnsError) as exc_info:
        validate_csv(content, filename="no_title.csv")
    assert "title" in exc_info.value.missing


def test_validate_csv_no_data_rows() -> None:
    # Header only, no data rows.
    content = b"title\n"
    with pytest.raises(CsvNoRowsError):
        validate_csv(content, filename="empty.csv")


def test_validate_csv_extra_columns_allowed() -> None:
    """Extra columns beyond the required ones are permitted."""
    content = _make_csv(
        [{"title": "RFP #1", "entity_name": "Austin ISD", "due_date": "2026-06-01"}],
        extra_header=["entity_name", "due_date"],
    )
    header, rows = validate_csv(content, filename="extra.csv")
    assert "entity_name" in header
    assert rows[0]["entity_name"] == "Austin ISD"


def test_validate_csv_bom_stripped() -> None:
    """UTF-8 BOM (byte-order mark) must be stripped before parsing."""
    # Prepend a BOM to the header.
    content = b"\xef\xbb\xbftitle\nBid #001\n"
    header, rows = validate_csv(content, filename="bom.csv")
    assert "title" in header
    assert rows[0]["title"] == "Bid #001"


# ---------------------------------------------------------------------------
# CSV row serialisation helper
# ---------------------------------------------------------------------------


def test_row_to_csv_bytes_round_trips() -> None:
    header = ["title", "entity_name", "due_date"]
    row = {"title": "RFP #1", "entity_name": "Austin ISD", "due_date": "2026-06-01"}
    result_bytes = _row_to_csv_bytes(header, row)
    # Re-parse and verify.
    import csv

    parsed = list(csv.DictReader(io.StringIO(result_bytes.decode("utf-8"))))
    assert len(parsed) == 1
    assert parsed[0]["title"] == "RFP #1"
    assert parsed[0]["entity_name"] == "Austin ISD"


# ---------------------------------------------------------------------------
# CSV import (full path — stores + enqueues per row)
# ---------------------------------------------------------------------------


class _FakeStoredRawDoc:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.recipe_id = "manual_upload:fake-ws:20260101T000000:abc123:row2"


class _FakeExtractionJob:
    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.status = "pending"


@pytest.mark.asyncio
async def test_csv_import_valid_rows_enqueued() -> None:
    """Valid rows are stored via D3 and enqueued for extraction (E1)."""
    ws_id = uuid.uuid4()
    content = _make_csv([{"title": "RFP #1"}, {"title": "RFP #2"}])
    fake_storage = MagicMock()
    fake_session = AsyncMock()

    fake_raw_doc = _FakeStoredRawDoc()
    fake_job = _FakeExtractionJob()

    with (
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "ingestion_services.store_raw_document",
            new=AsyncMock(return_value=fake_raw_doc),
        ) as mock_store,
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "extraction_services.enqueue_extraction",
            new=AsyncMock(return_value=fake_job),
        ) as mock_enqueue,
    ):
        result = await import_csv(
            fake_session,
            fake_storage,
            content=content,
            filename="test.csv",
            workspace_id=ws_id,
        )

    assert result.total_rows == 2
    assert len(result.job_ids) == 2
    assert result.skipped == []
    assert mock_store.call_count == 2
    assert mock_enqueue.call_count == 2


@pytest.mark.asyncio
async def test_csv_import_skips_empty_title_row() -> None:
    """A row with an empty 'title' column is skipped and reported."""
    ws_id = uuid.uuid4()
    content = _make_csv([{"title": "Valid Title"}, {"title": ""}, {"title": "  "}])
    fake_storage = MagicMock()
    fake_session = AsyncMock()

    fake_raw_doc = _FakeStoredRawDoc()
    fake_job = _FakeExtractionJob()

    with (
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "ingestion_services.store_raw_document",
            new=AsyncMock(return_value=fake_raw_doc),
        ),
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "extraction_services.enqueue_extraction",
            new=AsyncMock(return_value=fake_job),
        ),
    ):
        result = await import_csv(
            fake_session,
            fake_storage,
            content=content,
            filename="mixed.csv",
            workspace_id=ws_id,
        )

    assert result.total_rows == 3
    assert len(result.job_ids) == 1
    # Two skipped rows (empty + whitespace-only).
    assert len(result.skipped) == 2
    assert all("title" in s["reason"] for s in result.skipped)


@pytest.mark.asyncio
async def test_csv_import_skips_on_store_error() -> None:
    """A storage error on a row skips that row but continues the import."""
    ws_id = uuid.uuid4()
    content = _make_csv([{"title": "Good Row"}, {"title": "Bad Row"}])
    fake_storage = MagicMock()
    fake_session = AsyncMock()

    call_count = 0
    fake_job = _FakeExtractionJob()

    async def _store_side_effect(*args: Any, **kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("S3 unavailable")
        return _FakeStoredRawDoc()

    with (
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "ingestion_services.store_raw_document",
            new=AsyncMock(side_effect=_store_side_effect),
        ),
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "extraction_services.enqueue_extraction",
            new=AsyncMock(return_value=fake_job),
        ),
    ):
        result = await import_csv(
            fake_session,
            fake_storage,
            content=content,
            filename="partial.csv",
            workspace_id=ws_id,
        )

    assert result.total_rows == 2
    assert len(result.job_ids) == 1
    assert len(result.skipped) == 1
    assert "storage error" in result.skipped[0]["reason"]


@pytest.mark.asyncio
async def test_csv_import_raises_file_level_errors_before_storage() -> None:
    """File-level errors (too large, missing columns) are raised before any storage."""
    ws_id = uuid.uuid4()
    oversized = b"x" * (MAX_CSV_BYTES + 1)
    fake_storage = MagicMock()
    fake_session = AsyncMock()

    with patch(
        "civicsignals_api.modules.ingestion.manual_upload_services."
        "ingestion_services.store_raw_document",
        new=AsyncMock(),
    ) as mock_store:
        with pytest.raises(CsvTooLargeError):
            await import_csv(
                fake_session,
                fake_storage,
                content=oversized,
                filename="big.csv",
                workspace_id=ws_id,
            )
        # No storage calls should have happened.
        assert mock_store.call_count == 0


@pytest.mark.asyncio
async def test_csv_import_workspace_isolation() -> None:
    """recipe_id carries the workspace_id so rows from different workspaces don't share provenance."""
    ws1 = uuid.uuid4()
    ws2 = uuid.uuid4()
    content = _make_csv([{"title": "RFP #1"}])

    fake_storage = MagicMock()
    fake_session = AsyncMock()
    fake_job = _FakeExtractionJob()

    recipe_ids_seen: list[str] = []

    async def _capture_store(*args: Any, **kwargs: Any) -> Any:
        recipe_ids_seen.append(kwargs["recipe_id"])
        return _FakeStoredRawDoc()

    with (
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "ingestion_services.store_raw_document",
            new=AsyncMock(side_effect=_capture_store),
        ),
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "extraction_services.enqueue_extraction",
            new=AsyncMock(return_value=fake_job),
        ),
    ):
        await import_csv(
            fake_session,
            fake_storage,
            content=content,
            filename="import.csv",
            workspace_id=ws1,
        )
        await import_csv(
            fake_session,
            fake_storage,
            content=content,
            filename="import.csv",
            workspace_id=ws2,
        )

    assert len(recipe_ids_seen) == 2
    # recipe_ids must differ because they embed the workspace_id.
    assert recipe_ids_seen[0] != recipe_ids_seen[1]
    assert str(ws1) in recipe_ids_seen[0]
    assert str(ws2) in recipe_ids_seen[1]


# ---------------------------------------------------------------------------
# submit_raw_document (stored-doc path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_raw_document_enqueues_extraction() -> None:
    """submit_raw_document routes a stored doc into the extraction pipeline (E1)."""
    ws_id = uuid.uuid4()
    raw_doc_id = uuid.uuid4()

    fake_doc = MagicMock()
    fake_doc.id = raw_doc_id
    fake_doc.recipe_id = "foia_upload:some-req-id"

    fake_job = MagicMock()
    fake_job.id = uuid.uuid4()
    fake_job.status = "pending"

    fake_session = AsyncMock()

    with (
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "ingestion_services.get_raw_document",
            new=AsyncMock(return_value=fake_doc),
        ),
        patch(
            "civicsignals_api.modules.ingestion.manual_upload_services."
            "extraction_services.enqueue_extraction",
            new=AsyncMock(return_value=fake_job),
        ) as mock_enqueue,
    ):
        result = await submit_raw_document(
            fake_session,
            raw_document_id=raw_doc_id,
            workspace_id=ws_id,
        )

    assert result.raw_document_id == raw_doc_id
    assert result.job_id == fake_job.id
    assert result.was_new_job is True
    mock_enqueue.assert_called_once_with(
        fake_session,
        raw_doc_id,
        recipe_id=fake_doc.recipe_id,
    )


@pytest.mark.asyncio
async def test_submit_raw_document_not_found_raises() -> None:
    """submit_raw_document raises RawDocumentNotFoundError when the doc is absent."""
    ws_id = uuid.uuid4()
    raw_doc_id = uuid.uuid4()
    fake_session = AsyncMock()

    with patch(
        "civicsignals_api.modules.ingestion.manual_upload_services."
        "ingestion_services.get_raw_document",
        new=AsyncMock(return_value=None),
    ), pytest.raises(RawDocumentNotFoundError) as exc_info:
        await submit_raw_document(
            fake_session,
            raw_document_id=raw_doc_id,
            workspace_id=ws_id,
        )
    assert exc_info.value.raw_document_id == raw_doc_id


# ---------------------------------------------------------------------------
# Recipe schema validation: manual_upload connector_config validates
# ---------------------------------------------------------------------------


def test_recipe_schema_validates_manual_upload() -> None:
    """A recipe with connector=manual_upload validates against the recipe JSON Schema."""
    recipe = recipes_services.parse_recipe(
        {
            "recipe_id": "foia-upload-recipe",
            "connector": "manual_upload",
            "version": 1,
            "entity": {"name": "City of Seattle"},
            "connector_config": {
                "manual_upload": {"prefilter": "assume_relevant"}
            },
        }
    )
    assert recipe.connector == "manual_upload"


def test_recipe_schema_validates_manual_upload_no_config() -> None:
    """A recipe with connector=manual_upload and no config block validates."""
    recipe = recipes_services.parse_recipe(
        {
            "recipe_id": "foia-upload-no-config",
            "connector": "manual_upload",
            "version": 1,
            "entity": {"name": "Seattle School District"},
        }
    )
    assert recipe.connector == "manual_upload"
