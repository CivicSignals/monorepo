"""Services for the ``manual_upload`` connector (doc 18 §1 cat. H, §5 wave 5; D10).

Two input paths:

(a) **Uploaded FOIA responses** — accept a stored raw document (from M3's FOIA
    attachment / D3 storage) and route it through the extraction pipeline so a
    FOIA response PDF yields the same extracted/structured output as a scraped
    doc.  M3's ``foia.services.upload_attachment`` already wires the full path;
    :func:`submit_raw_document` exposes the generic seam for other callers.

(b) **Customer CSV import** — accept an uploaded CSV, validate size + columns,
    parse rows into normalized records, tolerating malformed rows (skip + report,
    never crash), store one raw document per row via D3, and enqueue each row
    into the extraction pipeline (E1) so downstream processing is identical to
    scraped content.

Both paths store bytes via :func:`ingestion.services.store_raw_document` (D3)
and enqueue via :func:`extraction.services.enqueue_extraction` (E1).  No
parallel extraction path is built — the shared pipeline is reused as-is.

Workspace-scoped: all functions accept a ``workspace_id`` that is stamped into
the raw-document ``metadata`` for provenance/analytics.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.modules.extraction import services as extraction_services
from civicsignals_api.modules.ingestion import services as ingestion_services

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants — validation limits
# ---------------------------------------------------------------------------

#: Maximum CSV file size accepted (10 MiB).
MAX_CSV_BYTES: int = 10 * 1024 * 1024

#: Maximum number of rows processed per CSV import (safety cap).
MAX_CSV_ROWS: int = 10_000

#: Columns always required in an import CSV.
REQUIRED_CSV_COLUMNS: frozenset[str] = frozenset({"title"})

#: Pseudo-recipe id for generic manual uploads (non-FOIA).  Workspace-scoped to
#: keep the (recipe_id, content_hash) dedupe key meaningful.
MANUAL_UPLOAD_RECIPE_PREFIX: str = "manual_upload"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ManualUploadError(Exception):
    """Base for all manual-upload validation / routing errors."""


class CsvTooLargeError(ManualUploadError):
    """The uploaded CSV exceeds :data:`MAX_CSV_BYTES`."""

    def __init__(self, size: int) -> None:
        super().__init__(
            f"CSV file is {size:,} bytes; the maximum allowed is {MAX_CSV_BYTES:,} bytes."
        )
        self.size = size


class CsvMissingColumnsError(ManualUploadError):
    """The uploaded CSV is missing one or more required columns."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__(
            f"CSV is missing required column(s): {missing!r}. "
            f"Required: {sorted(REQUIRED_CSV_COLUMNS)!r}."
        )
        self.missing = missing


class CsvNoRowsError(ManualUploadError):
    """The uploaded CSV has a header but no data rows."""


class RawDocumentNotFoundError(ManualUploadError):
    """A raw document id was supplied but no matching row exists (D3)."""

    def __init__(self, raw_document_id: uuid.UUID) -> None:
        super().__init__(f"Raw document {raw_document_id} not found in storage.")
        self.raw_document_id = raw_document_id


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CsvImportResult:
    """Outcome of a CSV import run.

    ``job_ids`` are the ``extraction_job`` ids created for valid rows (one per
    row), in row order.  ``skipped`` lists 1-indexed row numbers and the reason
    they were skipped (malformed, exceeded cap, etc.) — the import continues
    rather than crashing on bad rows.
    """

    job_ids: list[uuid.UUID] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    total_rows: int = 0


@dataclass
class SubmitDocumentResult:
    """Outcome of submitting a stored raw document for extraction."""

    job_id: uuid.UUID
    raw_document_id: uuid.UUID
    was_new_job: bool


# ---------------------------------------------------------------------------
# (a) Stored-document path — submit an already-stored raw document
# ---------------------------------------------------------------------------


async def submit_raw_document(
    session: AsyncSession,
    *,
    raw_document_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> SubmitDocumentResult:
    """Route a stored raw document (D3) through the extraction pipeline (E1; D10).

    The FOIA module (M3) calls this for FOIA attachment uploads via
    ``foia.services.upload_attachment`` — it already stores the bytes via D3 and
    calls ``extraction.services.enqueue_extraction`` directly.  This function is
    the **generic seam** for other callers (non-FOIA manual uploads) that have
    already stored bytes via :func:`ingestion.services.store_raw_document` and
    only need the extraction step.

    Raises :exc:`RawDocumentNotFoundError` if the raw document does not exist.
    The caller owns the transaction (this flushes, not commits).
    """
    doc = await ingestion_services.get_raw_document(session, raw_document_id)
    if doc is None:
        raise RawDocumentNotFoundError(raw_document_id)

    job = await extraction_services.enqueue_extraction(
        session,
        raw_document_id,
        recipe_id=doc.recipe_id,
    )

    log.info(
        "manual_upload.submit_raw_document",
        raw_document_id=str(raw_document_id),
        workspace_id=str(workspace_id),
        job_id=str(job.id),
        recipe_id=doc.recipe_id,
    )

    return SubmitDocumentResult(
        job_id=job.id,
        raw_document_id=raw_document_id,
        was_new_job=job.status == "pending",
    )


# ---------------------------------------------------------------------------
# (b) CSV import path — parse CSV → store + enqueue one raw doc per row
# ---------------------------------------------------------------------------


def validate_csv(
    content: bytes,
    *,
    filename: str = "import.csv",
) -> tuple[list[str], list[dict[str, str]]]:
    """Parse and validate a CSV upload, returning ``(header, rows)``.

    Raises:
        :exc:`CsvTooLargeError`: when ``len(content) > MAX_CSV_BYTES``.
        :exc:`CsvMissingColumnsError`: when required columns are absent.
        :exc:`CsvNoRowsError`: when the CSV has a header but no data rows.

    Does **not** raise on malformed individual rows — those are reported in the
    import result's ``skipped`` list so the import continues rather than crashing.
    Row-level errors are separated from file-level errors so callers can surface
    file-level problems before attempting any storage.
    """
    if len(content) > MAX_CSV_BYTES:
        raise CsvTooLargeError(len(content))

    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None:
        raise CsvMissingColumnsError(list(REQUIRED_CSV_COLUMNS))

    header = [str(f).strip() for f in reader.fieldnames]
    missing = [col for col in REQUIRED_CSV_COLUMNS if col not in header]
    if missing:
        raise CsvMissingColumnsError(sorted(missing))

    rows = list(reader)
    if not rows:
        raise CsvNoRowsError(
            f"CSV file {filename!r} has a header but no data rows."
        )

    return header, rows


async def import_csv(
    session: AsyncSession,
    storage: Any,
    *,
    content: bytes,
    filename: str,
    workspace_id: uuid.UUID,
    uploaded_by: uuid.UUID | None = None,
) -> CsvImportResult:
    """Import a CSV: validate, parse rows, store each as a raw document, enqueue.

    Each valid row is serialised back to a single-row CSV (so the extraction
    pipeline receives the same ``text/csv`` content type and the same normalised
    bytes it would for any other CSV upload) and stored via D3.  Malformed rows
    — missing required columns, blank ``title``, exceptions during serialisation
    — are skipped with a reason note rather than crashing the whole import.

    The per-row ``recipe_id`` is scoped to the workspace and the batch (the
    upload timestamp + filename) so the D3 ``(recipe_id, content_hash)`` dedupe
    key does not collapse distinct rows from different imports into the same
    provenance row.

    Raises file-level errors (:exc:`CsvTooLargeError`,
    :exc:`CsvMissingColumnsError`, :exc:`CsvNoRowsError`) **before** any
    storage; row-level errors are captured in :attr:`CsvImportResult.skipped`.

    The caller owns the transaction (this flushes rows but does not commit).
    """
    header, rows = validate_csv(content, filename=filename)

    batch_ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    # Unique batch id so two uploads of the same CSV don't dedupe to one job.
    batch_id = str(uuid.uuid4())[:8]
    recipe_prefix = f"{MANUAL_UPLOAD_RECIPE_PREFIX}:{workspace_id}:{batch_ts}:{batch_id}"

    result = CsvImportResult(total_rows=len(rows))

    for row_idx, row in enumerate(rows, start=2):  # row 1 = header
        if len(result.job_ids) >= MAX_CSV_ROWS:
            result.skipped.append(
                {"row": row_idx, "reason": f"exceeded max_rows cap ({MAX_CSV_ROWS})"}
            )
            continue

        # Required field validation.
        title = (row.get("title") or "").strip()
        if not title:
            result.skipped.append({"row": row_idx, "reason": "missing or empty 'title' column"})
            log.debug(
                "manual_upload.csv_import.skip_row",
                row=row_idx,
                reason="empty_title",
                workspace_id=str(workspace_id),
            )
            continue

        # Serialise the row back to a minimal CSV (header + this row only) so
        # the pipeline receives a well-formed CSV it can parse back.
        try:
            row_bytes = _row_to_csv_bytes(header, row)
        except Exception as exc:
            result.skipped.append({"row": row_idx, "reason": f"serialisation error: {exc}"})
            continue

        # Per-row recipe_id keeps the D3 dedup key meaningful.
        row_recipe_id = f"{recipe_prefix}:row{row_idx}"

        try:
            raw_doc = await ingestion_services.store_raw_document(
                session,
                storage,
                content=row_bytes,
                recipe_id=row_recipe_id,
                connector="manual_upload",
                source_url=f"csv_import://{workspace_id}/{filename}#row{row_idx}",
                content_type="text/csv",
                fetched_at=datetime.now(UTC),
                metadata={
                    "source": "csv_import",
                    "filename": filename,
                    "row_index": row_idx,
                    "workspace_id": str(workspace_id),
                    **({"uploaded_by": str(uploaded_by)} if uploaded_by else {}),
                    "title": title,
                },
            )
        except Exception as exc:
            result.skipped.append({"row": row_idx, "reason": f"storage error: {exc}"})
            log.warning(
                "manual_upload.csv_import.store_error",
                row=row_idx,
                error=str(exc),
                workspace_id=str(workspace_id),
            )
            continue

        try:
            job = await extraction_services.enqueue_extraction(
                session,
                raw_doc.id,
                recipe_id=row_recipe_id,
            )
        except Exception as exc:
            result.skipped.append({"row": row_idx, "reason": f"enqueue error: {exc}"})
            log.warning(
                "manual_upload.csv_import.enqueue_error",
                row=row_idx,
                error=str(exc),
                workspace_id=str(workspace_id),
            )
            continue

        result.job_ids.append(job.id)

    log.info(
        "manual_upload.csv_import.complete",
        filename=filename,
        workspace_id=str(workspace_id),
        total_rows=result.total_rows,
        enqueued=len(result.job_ids),
        skipped=len(result.skipped),
    )
    return result


def _row_to_csv_bytes(header: list[str], row: dict[str, str]) -> bytes:
    """Serialise one CSV row (with its header) to UTF-8 bytes."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore")
    writer.writeheader()
    writer.writerow(row)
    return buf.getvalue().encode("utf-8")


__all__ = [
    "MANUAL_UPLOAD_RECIPE_PREFIX",
    "MAX_CSV_BYTES",
    "MAX_CSV_ROWS",
    "REQUIRED_CSV_COLUMNS",
    "CsvImportResult",
    "CsvMissingColumnsError",
    "CsvNoRowsError",
    "CsvTooLargeError",
    "ManualUploadError",
    "RawDocumentNotFoundError",
    "SubmitDocumentResult",
    "import_csv",
    "submit_raw_document",
    "validate_csv",
]
