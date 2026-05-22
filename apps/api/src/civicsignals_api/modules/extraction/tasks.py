"""Celery tasks for the extraction module (doc 06 §8, doc 19 §1).

All tasks here are named ``extraction.*`` and therefore route to the ``extract``
queue (``celery_app.conf.task_routes``), drained by the ``worker_extract`` process
(doc 18 §6.2) — the image that carries the ``extraction`` extra (pdfplumber, the
LLM vendor SDKs).

E1 wires the orchestration chain (doc 19 §1, fetch -> parse -> relevance ->
extract -> score -> dedupe -> store) behind two tasks:

- ``extraction.process_document`` — runs the pipeline for one ``extraction_job``,
  with retry/backoff on transient failure and a dead-letter (``status=failed``)
  when retries are exhausted (mirrors D11's durable miss record).
- ``extraction.run_pending_documents`` — the every-1-min beat task; dispatches a
  ``process_document`` for each pending job.

The tasks are the thin process boundary: they own the DB session, the S3 storage,
and the job-status bookkeeping; the stage logic lives in ``pipeline.py`` and is
unit-tested without Celery.
"""

from __future__ import annotations

import asyncio
import uuid

import structlog

from civicsignals_api.celery_app import celery_app

from . import services
from .models import STAGE_EXTRACT, STAGE_STORE

log = structlog.get_logger(__name__)

# Celery retry policy for the per-document chain (doc 19 §1; the dead-letter
# pattern). Transient failures (LLM 5xx already retried inside the gateway, S3
# blips, DB contention) get a few backoff retries; once exhausted the job is
# dead-lettered with ``status=failed`` so the miss is durably visible and the
# raw snapshot stays replayable.
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 30  # seconds; 30s, 60s, 120s


@celery_app.task(
    name="extraction.process_document",
    bind=True,
    max_retries=MAX_RETRIES,
    acks_late=True,
)
def process_document(self, job_id: str, raw_document_id: str) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """Run the extraction funnel for one job (doc 19 §1; E1).

    Idempotent-ish: a re-run reuses the job row and re-stores candidates against
    the immutable S3 snapshot (E5 will dedupe). On a transient failure the task
    retries with exponential backoff; once ``max_retries`` is hit the job is
    dead-lettered (``status=failed``) and the error recorded.

    Thin shim over :func:`_run_process_document`, which holds the testable
    retry/dead-letter logic (so it can be exercised with a fake task ``self``
    instead of a real Celery broker).
    """
    return _run_process_document(self, job_id, raw_document_id)


def _run_process_document(task: object, job_id: str, raw_document_id: str) -> dict[str, object]:
    """The body of ``process_document`` (extracted so it is directly testable).

    ``task`` is the bound Celery task instance (``self``); only its ``request``
    (for the current retry count) and ``retry`` (to reschedule) are used, so a test
    can pass a small fake. Returns the run summary on success; on failure it either
    retries with exponential backoff (``self.retry`` raises ``Retry``) or, once
    retries are exhausted, dead-letters the job and re-raises.
    """
    job_uuid = uuid.UUID(job_id)
    doc_uuid = uuid.UUID(raw_document_id)
    try:
        return asyncio.run(_process_document_async(job_uuid, doc_uuid))
    except Exception as exc:  # we re-raise after bookkeeping
        retries: int = task.request.retries  # type: ignore[attr-defined]
        if retries < MAX_RETRIES:
            log.warning(
                "extraction.process_document.retry",
                job_id=job_id,
                attempt=retries + 1,
                error=str(exc),
            )
            raise task.retry(  # type: ignore[attr-defined]
                exc=exc, countdown=RETRY_BACKOFF_BASE * (2**retries)
            ) from exc
        # Retries exhausted: dead-letter the job (durable miss record).
        log.error(
            "extraction.process_document.dead_letter",
            job_id=job_id,
            raw_document_id=raw_document_id,
            error=str(exc),
        )
        asyncio.run(_dead_letter_async(job_uuid, error=str(exc)))
        raise


async def _process_document_async(
    job_id: uuid.UUID, raw_document_id: uuid.UUID
) -> dict[str, object]:
    # Imported here (not at module top) so importing this tasks module — which the
    # api process does via Celery autodiscovery — never eagerly builds the engine
    # or pulls the storage/gateway deps.
    from civicsignals_api.db import SessionLocal
    from civicsignals_api.modules.ingestion.services import RawDocumentStorage

    from .pipeline import run_extraction_pipeline

    storage = RawDocumentStorage.from_settings()
    prefilter = _resolve_prefilter_for_document(raw_document_id)

    async with SessionLocal() as session:
        await services.mark_job_running(session, job_id, stage=STAGE_EXTRACT)
        await session.commit()

        result = await run_extraction_pipeline(
            session,
            storage,
            job_id=job_id,
            raw_document_id=raw_document_id,
            prefilter=prefilter,
        )
        await services.mark_job_done(session, job_id, stage=STAGE_STORE, skipped=result.skipped)
        await session.commit()

    return {
        "job_id": str(job_id),
        "skipped": result.skipped,
        "relevant": result.relevant,
        "candidates": len(result.candidates),
    }


async def _dead_letter_async(job_id: uuid.UUID, *, error: str) -> None:
    from civicsignals_api.db import SessionLocal

    async with SessionLocal() as session:
        await services.mark_job_failed(session, job_id, stage=None, error=error)
        await session.commit()


def _resolve_prefilter_for_document(raw_document_id: uuid.UUID) -> str:
    """Resolve the recipe's relevance prefilter for a document (doc 19 §3.3).

    Best-effort: looks the document's recipe up and reads its ``prefilter`` so a
    pre-vetted source (``assume_relevant``) skips the LLM gate entirely. The recipe
    id lives on the job row we already created; the prefilter is a per-recipe
    posture. Defaults to ``classifier`` (run the gate) whenever the recipe can't be
    resolved — the safe, never-silently-drop default.
    """
    from civicsignals_api.modules.recipes import services as recipes_services

    # The job carries the recipe_id; resolve it without a DB round-trip by reading
    # the recipe definition. We need the recipe_id, so fetch the job synchronously
    # via a short-lived session.
    recipe_id = asyncio.run(_recipe_id_for_job(raw_document_id))
    if recipe_id is None:
        return services.PREFILTER_CLASSIFIER
    try:
        recipe = recipes_services.load_recipe(recipe_id)
    except Exception:  # any load failure -> safe default
        return services.PREFILTER_CLASSIFIER
    return recipe.prefilter


async def _recipe_id_for_job(raw_document_id: uuid.UUID) -> str | None:
    from sqlalchemy import select

    from civicsignals_api.db import SessionLocal

    from .models import ExtractionJob

    async with SessionLocal() as session:
        return (
            await session.execute(
                select(ExtractionJob.recipe_id).where(
                    ExtractionJob.raw_document_id == raw_document_id
                )
            )
        ).scalar_one_or_none()


# How many freshly-fetched raw documents one beat tick turns into jobs. A backlog
# is drained over successive ticks rather than in one burst (doc 06 §8: every 1m).
DISCOVER_BATCH = 200


@celery_app.task(name="extraction.run_pending_documents")
def run_pending_documents() -> int:
    """Create + dispatch extraction jobs for new raw documents (doc 06 §8; 1 min).

    The beat task at the head of the funnel. Two steps:

    1. **Discover** freshly-fetched ``ingestion_raw_document`` rows (via ingestion's
       service seam — doc 06 §3) and create an ``extraction_job`` per document. The
       job table's UNIQUE on ``raw_document_id`` makes this idempotent: a document
       already enqueued is a no-op, so a re-scan never double-processes.
    2. **Dispatch** ``process_document`` for every pending job into the ``extract``
       queue. Each runs through the relevance gate (doc 19 §3, E8) before any
       expensive extraction — the funnel's largest cost lever.

    Returns the number of jobs dispatched this tick (0 when nothing is pending).
    """
    return asyncio.run(_run_pending_documents_async())


async def _run_pending_documents_async() -> int:
    from civicsignals_api.db import SessionLocal
    from civicsignals_api.modules.ingestion import services as ingestion_services

    async with SessionLocal() as session:
        # 1. Discover new raw documents and create pending jobs (idempotent).
        refs = await ingestion_services.list_raw_document_refs(session, limit=DISCOVER_BATCH)
        for ref in refs:
            await services.enqueue_extraction(session, ref.id, recipe_id=ref.recipe_id)
        await session.commit()

        # 2. Dispatch a per-document task for every pending job.
        pending = await services.list_pending_documents(session)

    for job in pending:
        process_document.delay(str(job.id), str(job.raw_document_id))
    return len(pending)


def enqueue_extraction(raw_document_id: uuid.UUID, *, recipe_id: str) -> None:
    """Create a job for a stored document and dispatch its pipeline task (E1).

    The Celery-aware trigger seam ingestion / the recipe runner call right after
    storing a raw document (the ``# TODO E1`` marker in ``ingestion.services``).
    Wraps ``services.enqueue_extraction`` (the get-or-create) and then dispatches
    ``process_document``. Kept separate from the service function so the service
    layer stays Celery-free and unit-testable.
    """
    job = asyncio.run(_enqueue_extraction_async(raw_document_id, recipe_id=recipe_id))
    process_document.delay(str(job.id), str(job.raw_document_id))


async def _enqueue_extraction_async(raw_document_id: uuid.UUID, *, recipe_id: str):  # type: ignore[no-untyped-def]
    from civicsignals_api.db import SessionLocal

    async with SessionLocal() as session:
        job = await services.enqueue_extraction(session, raw_document_id, recipe_id=recipe_id)
        await session.commit()
        return job
