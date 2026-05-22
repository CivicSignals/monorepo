"""HTTP endpoints for the ingestion module, mounted under `/api/v1/ingestion`.

D10 — ``manual_upload`` connector (doc 18 §1 cat. H, §5 wave 5):

  ``POST /api/v1/ingestion/manual-upload/documents/{raw_document_id}/extract``
        Submit an already-stored raw document for extraction.
        Workspace-scoped; the raw document must exist in D3 storage.

  ``POST /api/v1/ingestion/manual-upload/csv``
        Upload a CSV and import each row through the extraction pipeline.
        Workspace-scoped; the file is validated then each row is stored via D3
        and enqueued for extraction (E1).

Both endpoints follow RFC 7807 ``application/problem+json`` errors and require
the ``X-Workspace-Id`` header (``CurrentWorkspace`` dependency).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.db import get_session
from civicsignals_api.modules.auth.dependencies import CurrentWorkspace
from civicsignals_api.problems import ProblemException

from . import manual_upload_services as mu_services

router = APIRouter(prefix="/ingestion", tags=["ingestion"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SubmitDocumentResponse(BaseModel):
    """Response for a manual extraction submission (D10)."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID = Field(description="The extraction job id created for this document.")
    raw_document_id: uuid.UUID = Field(description="The raw document id that was submitted.")
    was_new_job: bool = Field(
        description=(
            "True when a new extraction job was created; False when the document already "
            "had an existing extraction job (idempotent re-submission)."
        )
    )


class SkippedRow(BaseModel):
    """One skipped CSV row and the reason it was skipped (D10)."""

    model_config = ConfigDict(extra="forbid")

    row: int = Field(description="1-based row index in the CSV (row 1 = header, row 2 = first data row).")
    reason: str = Field(description="Human-readable reason this row was skipped.")


class CsvImportResponse(BaseModel):
    """Response for a CSV import (D10)."""

    model_config = ConfigDict(extra="forbid")

    total_rows: int = Field(description="Total number of data rows in the CSV (excluding the header).")
    enqueued: int = Field(description="Number of rows successfully stored and enqueued for extraction.")
    skipped_count: int = Field(description="Number of rows that were skipped (malformed, capped, etc.).")
    job_ids: list[uuid.UUID] = Field(
        description="Extraction job ids created for valid rows, in row order.",
    )
    skipped: list[SkippedRow] = Field(
        default_factory=list,
        description="Details of each skipped row (row number + reason).",
    )


# ---------------------------------------------------------------------------
# Error helpers
# ---------------------------------------------------------------------------


def _raw_doc_not_found(raw_document_id: uuid.UUID) -> ProblemException:
    return ProblemException(
        status=404,
        code="raw_document_not_found",
        title="Raw document not found",
        detail=f"No raw document with id {raw_document_id} exists in storage.",
    )


def _csv_too_large(size: int) -> ProblemException:
    return ProblemException(
        status=413,
        code="csv_too_large",
        title="CSV file too large",
        detail=(
            f"The uploaded CSV is {size:,} bytes; the maximum allowed is "
            f"{mu_services.MAX_CSV_BYTES:,} bytes ({mu_services.MAX_CSV_BYTES // 1024 // 1024} MiB)."
        ),
    )


def _csv_missing_columns(missing: list[str]) -> ProblemException:
    return ProblemException(
        status=422,
        code="csv_missing_columns",
        title="CSV is missing required columns",
        detail=(
            f"The CSV is missing required column(s): {missing!r}. "
            f"Required columns: {sorted(mu_services.REQUIRED_CSV_COLUMNS)!r}."
        ),
    )


def _csv_no_rows() -> ProblemException:
    return ProblemException(
        status=422,
        code="csv_no_rows",
        title="CSV has no data rows",
        detail="The uploaded CSV has a header row but no data rows to import.",
    )


# ---------------------------------------------------------------------------
# (a) Submit a stored raw document for extraction
# ---------------------------------------------------------------------------


@router.post(
    "/manual-upload/documents/{raw_document_id}/extract",
    response_model=SubmitDocumentResponse,
    status_code=202,
    summary="Submit a stored raw document for manual extraction",
    description=(
        "Route a raw document already stored in content-addressable storage (D3) through "
        "the extraction pipeline (E1) as if it were a scraped document.  "
        "Workspace-scoped.  Idempotent: re-submitting a document that already has an "
        "extraction job returns the existing job rather than creating a duplicate.\n\n"
        "The primary use-case is re-running extraction against a stored FOIA response PDF "
        "after a recipe fix, or submitting a document uploaded outside of the FOIA module."
    ),
)
async def submit_raw_document_for_extraction(
    raw_document_id: uuid.UUID,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> SubmitDocumentResponse:
    """Submit a stored raw document for extraction (D10)."""
    try:
        result = await mu_services.submit_raw_document(
            session,
            raw_document_id=raw_document_id,
            workspace_id=ctx.workspace_id,
        )
    except mu_services.RawDocumentNotFoundError:
        raise _raw_doc_not_found(raw_document_id) from None

    await session.commit()
    return SubmitDocumentResponse(
        job_id=result.job_id,
        raw_document_id=result.raw_document_id,
        was_new_job=result.was_new_job,
    )


# ---------------------------------------------------------------------------
# (b) CSV import — upload + store + enqueue each row
# ---------------------------------------------------------------------------


@router.post(
    "/manual-upload/csv",
    response_model=CsvImportResponse,
    status_code=202,
    summary="Import a CSV through the extraction pipeline",
    description=(
        "Upload a CSV file and route each valid row through the extraction pipeline (E1) "
        "as if it were a scraped document.  Each row is stored as a raw document in "
        "content-addressable storage (D3) and enqueued for extraction.  "
        "Workspace-scoped.\n\n"
        f"**Required columns:** `{sorted(mu_services.REQUIRED_CSV_COLUMNS)!r}`\n\n"
        f"**Size limit:** {mu_services.MAX_CSV_BYTES // 1024 // 1024} MiB "
        f"({mu_services.MAX_CSV_BYTES:,} bytes)\n\n"
        f"**Row limit:** {mu_services.MAX_CSV_ROWS:,} rows per import\n\n"
        "Malformed rows (missing required columns, blank title, serialisation errors) are "
        "**skipped with a reason note** rather than failing the whole import.  "
        "The response lists every skipped row so callers can investigate."
    ),
)
async def import_csv(
    file: UploadFile,
    ctx: CurrentWorkspace,
    session: SessionDep,
) -> CsvImportResponse:
    """Import a CSV: validate, store each row via D3, enqueue each for extraction (D10)."""
    import functools

    @functools.lru_cache(maxsize=1)
    def _get_storage() -> object:
        from civicsignals_api.modules.ingestion.storage import RawDocumentStorage

        return RawDocumentStorage.from_settings()

    content = await file.read()
    filename = file.filename or "import.csv"

    storage = _get_storage()

    try:
        result = await mu_services.import_csv(
            session,
            storage,
            content=content,
            filename=filename,
            workspace_id=ctx.workspace_id,
            uploaded_by=ctx.user.id,
        )
    except mu_services.CsvTooLargeError as exc:
        raise _csv_too_large(exc.size) from None
    except mu_services.CsvMissingColumnsError as exc:
        raise _csv_missing_columns(exc.missing) from None
    except mu_services.CsvNoRowsError:
        raise _csv_no_rows() from None

    await session.commit()

    return CsvImportResponse(
        total_rows=result.total_rows,
        enqueued=len(result.job_ids),
        skipped_count=len(result.skipped),
        job_ids=result.job_ids,
        skipped=[SkippedRow(row=s["row"], reason=s["reason"]) for s in result.skipped],
    )
