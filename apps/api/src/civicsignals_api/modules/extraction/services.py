"""Public service interface for the extraction module.

Other modules call extraction only through the functions/classes re-exported
here — never by importing extraction's models or routes directly (doc 06 §3).

E8 lands the Stage-2 relevance gate (doc 19 §3): a cheap LLM that decides whether
a fetched document is worth running through full extraction.

E1 lands the orchestration: the Celery chain that turns a stored raw document into
candidate signals (doc 19 §1, fetch -> parse -> relevance -> extract -> score ->
dedupe -> store). Ingestion / the recipe runner / the beat task trigger it through
:func:`enqueue_extraction`; the job lifecycle is observable via
``extraction_job`` and the candidates land in ``extraction_candidate`` for E4/E5.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    JOB_STATUS_DONE,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SKIPPED_IRRELEVANT,
    ExtractionJob,
)
from .relevance import (
    PREFILTER_ASSUME_RELEVANT,
    PREFILTER_CLASSIFIER,
    RelevanceClassifier,
    truncate_document,
)
from .schemas import (
    RELEVANCE_CATEGORIES,
    CandidateRecord,
    DocumentRef,
    ExtractionCandidateRecord,
    ExtractionJobRecord,
    ParsedDocument,
    RelevanceDecisionRecord,
    RelevanceVerdict,
)


async def enqueue_extraction(
    session: AsyncSession,
    raw_document_id: uuid.UUID,
    *,
    recipe_id: str,
) -> ExtractionJobRecord:
    """Trigger the extraction pipeline for a stored raw document (doc 19 §1; E1).

    The seam ingestion / the recipe runner / the ``run_pending_documents`` beat
    task call after a raw document is stored. Idempotent: a get-or-create on
    ``raw_document_id`` (UNIQUE), so re-enqueuing the same document returns its
    existing job rather than forking the funnel — under concurrency the loser of
    the ``ON CONFLICT DO NOTHING`` race re-selects the winner's row.

    This **does not** dispatch the Celery task itself (it has no Celery import, so
    it stays usable from any process and from tests). The task layer
    (``tasks.enqueue_extraction``) wraps this with ``process_document.delay(...)``.
    The caller owns the transaction (this flushes, not commits).
    """
    stmt = (
        pg_insert(ExtractionJob)
        .values(
            id=uuid.uuid4(),
            raw_document_id=raw_document_id,
            recipe_id=recipe_id,
            status=JOB_STATUS_PENDING,
        )
        .on_conflict_do_nothing(constraint="extraction_job_raw_document_uq")
        .returning(ExtractionJob.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is None:
        existing = await _find_job_by_document(session, raw_document_id)
        assert existing is not None  # the conflicting row must exist post-insert
        return ExtractionJobRecord.model_validate(existing)
    row = await session.get(ExtractionJob, inserted_id)
    assert row is not None
    return ExtractionJobRecord.model_validate(row)


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> ExtractionJobRecord | None:
    """Fetch an extraction job by id (observability seam; doc 19 §12.1)."""
    row = await session.get(ExtractionJob, job_id)
    return ExtractionJobRecord.model_validate(row) if row is not None else None


async def list_pending_documents(
    session: AsyncSession, *, limit: int = 100
) -> list[ExtractionJobRecord]:
    """List pending jobs (read-only observability; oldest first).

    For the dispatch path the beat task uses :func:`claim_pending_jobs`, which
    *atomically* transitions the jobs it returns out of ``pending`` so they can't
    be dispatched twice. This read-only listing is kept for inspection/tests.
    """
    rows = (
        (
            await session.execute(
                select(ExtractionJob)
                .where(ExtractionJob.status == JOB_STATUS_PENDING)
                .order_by(ExtractionJob.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [ExtractionJobRecord.model_validate(r) for r in rows]


async def claim_pending_jobs(
    session: AsyncSession, *, limit: int = 200
) -> list[ExtractionJobRecord]:
    """Atomically claim a batch of pending jobs for dispatch (doc 06 §8).

    The dispatch path of the ``run_pending_documents`` beat task. Selecting
    ``pending`` jobs and *immediately* transitioning them to ``running`` in one
    statement (oldest first, ``FOR UPDATE SKIP LOCKED``) closes the duplicate-
    dispatch window: a job that sits in the broker for longer than the 1-minute
    beat cadence is no longer ``pending`` on the next tick, so it is dispatched
    exactly once. ``SKIP LOCKED`` also makes two overlapping beat ticks safe — each
    claims a disjoint set rather than blocking or double-claiming. The caller owns
    the transaction (this flushes, not commits); ``process_document``'s own
    ``mark_job_running`` is then an idempotent re-affirm that bumps ``attempts``.
    """
    # Select-and-lock the oldest pending jobs, skipping rows another worker/tick
    # already holds, then flip them to running in the same unit of work.
    locked = (
        (
            await session.execute(
                select(ExtractionJob)
                .where(ExtractionJob.status == JOB_STATUS_PENDING)
                .order_by(ExtractionJob.created_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    claimed: list[ExtractionJobRecord] = []
    for row in locked:
        row.status = JOB_STATUS_RUNNING
        claimed.append(ExtractionJobRecord.model_validate(row))
    await session.flush()
    return claimed


async def mark_job_running(
    session: AsyncSession, job_id: uuid.UUID, *, stage: str | None = None
) -> None:
    """Move a job to ``running`` and bump its attempt counter (doc 19 §12.1)."""
    row = await session.get(ExtractionJob, job_id)
    if row is None:
        return
    row.status = JOB_STATUS_RUNNING
    row.attempts += 1
    if stage is not None:
        row.stage = stage
    row.error = None
    await session.flush()


async def mark_job_done(
    session: AsyncSession, job_id: uuid.UUID, *, stage: str, skipped: bool
) -> None:
    """Move a job to a success terminal state (``done`` or ``skipped_irrelevant``).

    ``skipped`` records that the relevance gate dropped the document — a success,
    not a failure (doc 19 §3.2).
    """
    row = await session.get(ExtractionJob, job_id)
    if row is None:
        return
    row.status = JOB_STATUS_SKIPPED_IRRELEVANT if skipped else JOB_STATUS_DONE
    row.stage = stage
    row.error = None
    await session.flush()


async def mark_job_failed(
    session: AsyncSession, job_id: uuid.UUID, *, stage: str | None, error: str
) -> None:
    """Move a job to ``failed`` with the error message — the dead-letter record.

    Called by the task once Celery's retries are exhausted (mirrors D11's durable
    miss record). The job stays in the table so the failure is replayable against
    the S3 snapshot (doc 19 §1).
    """
    row = await session.get(ExtractionJob, job_id)
    if row is None:
        return
    row.status = JOB_STATUS_FAILED
    if stage is not None:
        row.stage = stage
    # Truncate to keep a runaway traceback from bloating the row.
    row.error = error[:4000]
    await session.flush()


async def _find_job_by_document(
    session: AsyncSession, raw_document_id: uuid.UUID
) -> ExtractionJob | None:
    return (
        await session.execute(
            select(ExtractionJob).where(ExtractionJob.raw_document_id == raw_document_id)
        )
    ).scalar_one_or_none()


__all__ = [
    "PREFILTER_ASSUME_RELEVANT",
    "PREFILTER_CLASSIFIER",
    "RELEVANCE_CATEGORIES",
    "CandidateRecord",
    "DocumentRef",
    "ExtractionCandidateRecord",
    "ExtractionJobRecord",
    "ParsedDocument",
    "RelevanceClassifier",
    "RelevanceDecisionRecord",
    "RelevanceVerdict",
    "claim_pending_jobs",
    "enqueue_extraction",
    "get_job",
    "list_pending_documents",
    "mark_job_done",
    "mark_job_failed",
    "mark_job_running",
    "truncate_document",
]
