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

    async with SessionLocal() as session:
        prefilter = await _resolve_prefilter_for_job(session, raw_document_id)
        confidence_config = await _resolve_confidence_config_for_job(session, raw_document_id)
        await services.mark_job_running(session, job_id, stage=STAGE_EXTRACT)
        await session.commit()

        result = await run_extraction_pipeline(
            session,
            storage,
            job_id=job_id,
            raw_document_id=raw_document_id,
            prefilter=prefilter,
            confidence_config=confidence_config,
        )
        await services.mark_job_done(session, job_id, stage=STAGE_STORE, skipped=result.skipped)
        await session.commit()

    # F3 scoring trigger (doc 14 §4.1, §6): the candidates committed above produced
    # global ``signals_signal`` rows but nothing has scored them per workspace yet.
    # The in-process ``signal.created`` event bus has no subscriber in this
    # ``worker_extract`` process (only the FastAPI app factory registers the scoring
    # listener), so we enqueue ``signals.score_signal`` per new signal onto the
    # ``score`` queue — the fan-out runs in ``worker_score`` (doc 18 §6.2). Enqueue
    # *after commit* so the signal row is visible when the score task reads it; the
    # score task's upsert is idempotent on re-delivery. This is the sole trigger —
    # we do not also publish ``signal.created`` here, so each signal is scored once.
    _enqueue_scoring(result.signal_ids)

    return {
        "job_id": str(job_id),
        "skipped": result.skipped,
        "relevant": result.relevant,
        "candidates": len(result.candidates),
        "signals_scored": len(result.signal_ids),
    }


def _enqueue_scoring(signal_ids: list[uuid.UUID]) -> None:
    """Enqueue one ``signals.score_signal`` task per newly-promoted signal (F3).

    Lazy import of the signals task so the extraction tasks module stays importable
    without pulling the signals task graph at import time, and so the cross-module
    dependency is a Celery enqueue (the ``score`` queue) rather than a direct call.
    """
    if not signal_ids:
        return
    from civicsignals_api.modules.signals.tasks import score_signal

    # Best-effort: the signals are already committed, so a broker blip here must
    # NOT escape and trigger a full extraction retry (which would re-run the LLM
    # and dead-letter a job whose signals exist). A missed enqueue leaves the
    # signal unscored until the next ICP-change backfill or a manual re-score;
    # we log it loudly so it's observable.
    for signal_id in signal_ids:
        try:
            score_signal.delay(str(signal_id))
        except Exception:  # enqueue must not undo already-committed signal work
            log.warning("extraction.score_enqueue_failed", signal_id=str(signal_id))


async def _dead_letter_async(job_id: uuid.UUID, *, error: str) -> None:
    from civicsignals_api.db import SessionLocal

    async with SessionLocal() as session:
        await services.mark_job_failed(session, job_id, stage=None, error=error)
        await session.commit()


async def _resolve_prefilter_for_job(session, raw_document_id: uuid.UUID) -> str:  # type: ignore[no-untyped-def]
    """Resolve the recipe's relevance prefilter for a document (doc 19 §3.3).

    Best-effort and **async** — it runs inside the task's existing event loop and
    session (never a nested ``asyncio.run``). Reads the job's ``recipe_id`` (the
    job row already exists) and loads the recipe definition to read its
    ``prefilter`` so a pre-vetted source (``assume_relevant``) skips the LLM gate
    entirely. Defaults to ``classifier`` (run the gate) whenever the recipe can't be
    resolved — the safe, never-silently-drop default.
    """
    from sqlalchemy import select

    from civicsignals_api.modules.recipes import services as recipes_services

    from .models import ExtractionJob

    recipe_id = (
        await session.execute(
            select(ExtractionJob.recipe_id).where(ExtractionJob.raw_document_id == raw_document_id)
        )
    ).scalar_one_or_none()
    if recipe_id is None:
        return services.PREFILTER_CLASSIFIER
    try:
        recipe = recipes_services.load_recipe(recipe_id)
    except Exception:  # any load failure -> safe default
        return services.PREFILTER_CLASSIFIER
    return recipe.prefilter


async def _resolve_confidence_config_for_job(session, raw_document_id: uuid.UUID):  # type: ignore[no-untyped-def]
    """Resolve the recipe's confidence-scoring config for a document (doc 19 §6.3).

    The recipe-configurable thresholds + source-quality tier (E6, doc 19 §6.3): an
    authoritative source raises the band floors, a noisy aggregator lowers them.

    # TODO E6 (recipe plumbing): the ``Recipe`` schema (recipes module +
    # packages/recipe-schema) has no typed ``confidence:`` block yet, so there is no
    # per-recipe override to read — every job uses the documented §6.2/§6.3 default.
    # This is the seam: once the typed recipe field lands, read it here and pass it to
    # ``signals.services.config_from_recipe``. Defaults to the documented baseline
    # whenever the recipe can't be resolved.
    """
    from civicsignals_api.modules.signals.services import DEFAULT_CONFIG

    return DEFAULT_CONFIG


# Page size for one keyset discovery batch (and one claim+dispatch batch).
DISCOVER_BATCH = 200
# Per-tick cap on documents scanned/enqueued, so a large backlog is drained over
# successive ticks (doc 06 §8: every 1m) rather than in one unbounded burst.
DISCOVER_MAX_PER_TICK = 2_000


@celery_app.task(name="extraction.run_pending_documents")
def run_pending_documents() -> int:
    """Create + dispatch extraction jobs for new raw documents (doc 06 §8; 1 min).

    The beat task at the head of the funnel. Two steps:

    1. **Discover** freshly-fetched ``ingestion_raw_document`` rows (via ingestion's
       service seam — doc 06 §3) and create an ``extraction_job`` per document.
       Discovery pages forward with a keyset cursor on ``created_at`` so a backlog
       larger than one batch isn't starved (it advances past already-enqueued rows
       instead of re-scanning the same oldest ``DISCOVER_BATCH`` every tick), up to
       ``DISCOVER_MAX_PER_TICK``. The job table's UNIQUE on ``raw_document_id``
       makes enqueue idempotent: a document already enqueued is a no-op.
    2. **Claim + dispatch**: :func:`services.claim_pending_jobs` atomically moves
       the jobs it returns out of ``pending`` (``FOR UPDATE SKIP LOCKED``), so a job
       that lingers in the broker past the 1-minute cadence is dispatched exactly
       once — no duplicate concurrent processing. Each runs the relevance gate
       (doc 19 §3, E8) before any expensive extraction — the funnel's cost lever.

    Returns the number of jobs dispatched this tick (0 when nothing is pending).
    """
    return asyncio.run(_run_pending_documents_async())


async def _run_pending_documents_async() -> int:
    from datetime import datetime

    from civicsignals_api.db import SessionLocal
    from civicsignals_api.modules.ingestion import services as ingestion_services

    async with SessionLocal() as session:
        # 1. Discover new raw documents and create pending jobs (idempotent).
        # Page forward on the composite (created_at, id) keyset cursor so we don't
        # re-scan the same oldest rows every tick; enqueue is idempotent so already-
        # known docs are cheap no-ops, but advancing the cursor lets newer docs
        # through. The id tiebreaker prevents skipping same-timestamp rows.
        cursor_ts: datetime | None = None
        cursor_id: uuid.UUID | None = None
        scanned = 0
        while scanned < DISCOVER_MAX_PER_TICK:
            refs = await ingestion_services.list_raw_document_refs(
                session, limit=DISCOVER_BATCH, after=cursor_ts, after_id=cursor_id
            )
            if not refs:
                break
            for ref in refs:
                await services.enqueue_extraction(session, ref.id, recipe_id=ref.recipe_id)
            scanned += len(refs)
            cursor_ts = refs[-1].created_at
            cursor_id = refs[-1].id
            if len(refs) < DISCOVER_BATCH:
                break  # drained the table
        await session.commit()

        # 2. Atomically claim a batch of pending jobs (moves them out of pending),
        # then dispatch one task each. Claiming closes the duplicate-dispatch window.
        claimed = await services.claim_pending_jobs(session, limit=DISCOVER_BATCH)
        await session.commit()

    for job in claimed:
        process_document.delay(str(job.id), str(job.raw_document_id))
    return len(claimed)


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
