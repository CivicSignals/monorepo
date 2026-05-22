"""Tests for the extraction Celery tasks' retry / dead-letter wiring (E1).

The stage logic is tested in ``test_pipeline_*``; here we verify the *task*
boundary: the bound ``process_document`` retries a transient failure with backoff
and dead-letters (records ``status=failed``) once retries are exhausted, without a
real broker or database. We drive the task function directly with a fake ``self``
and monkeypatch the async helpers it calls.
"""

from __future__ import annotations

import uuid

import pytest

from civicsignals_api.modules.extraction import tasks


class _Retry(Exception):
    """Stand-in for ``celery.Task.retry`` raising to abort the current attempt."""


class _FakeRequest:
    def __init__(self, retries: int) -> None:
        self.retries = retries


class _FakeTask:
    """Mimics the ``bind=True`` ``self`` Celery passes: ``request`` + ``retry``."""

    def __init__(self, retries: int) -> None:
        self.request = _FakeRequest(retries)
        self.retried_with: dict[str, object] | None = None

    def retry(self, *, exc: BaseException, countdown: int) -> _Retry:
        self.retried_with = {"exc": exc, "countdown": countdown}
        # Celery's real retry raises Retry; we mirror that so control leaves the task.
        return _Retry()


def test_process_document_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    async def _ok(j: uuid.UUID, d: uuid.UUID) -> dict[str, object]:
        return {"job_id": str(j), "skipped": False, "candidates": 1}

    monkeypatch.setattr(tasks, "_process_document_async", _ok)

    fake_self = _FakeTask(retries=0)
    result = tasks._run_process_document(fake_self, str(job_id), str(doc_id))
    assert result["candidates"] == 1
    assert fake_self.retried_with is None


def test_process_document_retries_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(j: uuid.UUID, d: uuid.UUID) -> dict[str, object]:
        raise RuntimeError("transient: S3 hiccup")

    monkeypatch.setattr(tasks, "_process_document_async", _boom)

    fake_self = _FakeTask(retries=0)  # first attempt -> should retry, not dead-letter
    with pytest.raises(_Retry):
        tasks._run_process_document(fake_self, str(uuid.uuid4()), str(uuid.uuid4()))
    assert fake_self.retried_with is not None
    assert isinstance(fake_self.retried_with["exc"], RuntimeError)
    # First retry backs off RETRY_BACKOFF_BASE * 2**0 == base.
    assert fake_self.retried_with["countdown"] == tasks.RETRY_BACKOFF_BASE


def test_process_document_dead_letters_when_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = uuid.uuid4()
    dead_lettered: dict[str, object] = {}

    async def _boom(j: uuid.UUID, d: uuid.UUID) -> dict[str, object]:
        raise RuntimeError("permanent failure")

    async def _dead_letter(j: uuid.UUID, *, error: str) -> None:
        dead_lettered["job_id"] = j
        dead_lettered["error"] = error

    monkeypatch.setattr(tasks, "_process_document_async", _boom)
    monkeypatch.setattr(tasks, "_dead_letter_async", _dead_letter)

    # retries == MAX_RETRIES -> no more retries; the task dead-letters and re-raises.
    fake_self = _FakeTask(retries=tasks.MAX_RETRIES)
    with pytest.raises(RuntimeError, match="permanent failure"):
        tasks._run_process_document(fake_self, str(job_id), str(uuid.uuid4()))
    assert fake_self.retried_with is None  # did not retry
    assert dead_lettered["job_id"] == job_id
    assert "permanent failure" in str(dead_lettered["error"])
