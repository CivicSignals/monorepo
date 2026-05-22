"""Orchestrator tests for ``run_extraction_pipeline`` (doc 19 §1; E1).

These drive the full fetch -> parse -> relevance -> extract -> score -> dedupe ->
store chain without a real database, using:

- a ``FakeBackend`` gateway (relevance + extract calls, no network),
- a fake :class:`RawDocumentStorage` returning seeded bytes,
- a fake ingestion ``get_raw_document`` (monkeypatched) returning a seeded row,
- a minimal fake ``AsyncSession`` that records ``add()``\\ed rows.

The relevance-gate-drop path (no extract call) and the persistence shape are the
key behaviours. The live-Postgres round-trip is in ``test_pipeline_persistence.py``.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from civicsignals_api.llm_gateway import (
    TASK_CLASSIFY,
    TASK_EXTRACTION,
    FakeBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.extraction import pipeline
from civicsignals_api.modules.extraction.models import ExtractionCandidate
from civicsignals_api.modules.extraction.relevance import RelevanceClassifier
from civicsignals_api.modules.ingestion.services import StoredRawDocument


class _FakeSession:
    """A minimal stand-in for AsyncSession recording added rows (no DB).

    ``run_extraction_pipeline`` only calls ``add`` + ``flush`` on the session (the
    relevance-decision record and the candidate rows). ``flush`` assigns ids so
    the rows look persisted. We do not exercise ``get``/``execute`` here.
    """

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "__table__"):
                obj.id = uuid.uuid4()  # type: ignore[attr-defined]


class _FakeStorage:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def get_document(self, key: str) -> bytes:
        return self._content


def _stored_doc() -> StoredRawDocument:
    return StoredRawDocument(
        id=uuid.uuid4(),
        recipe_id="seattle_school_board_agendas",
        recipe_version=1,
        connector="http_static",
        source_url="https://example.gov/agenda",
        fetched_at=datetime.now(UTC),
        content_hash="0" * 64,
        blob_key="sha256/" + "0" * 64,
        content_type="text/plain",
    )


def _gateway(*, relevance: str, extract: str) -> tuple[LLMGateway, FakeBackend]:
    """A gateway whose classify task and extraction task return scripted JSON.

    Two backends so the FIFO response queues don't interleave between the two
    tasks: TASK_CLASSIFY -> the relevance backend, TASK_EXTRACTION -> the extract
    backend. Returns the gateway plus the extract backend so a test can assert
    whether the (expensive) extract call ever fired.
    """
    extract_be = FakeBackend(provider="extract_be", responses=[extract])
    policy = TaskModelPolicy(
        overrides={
            TASK_CLASSIFY: ModelChoice("relevance_be", "haiku"),
            TASK_EXTRACTION: ModelChoice("extract_be", "sonnet"),
        }
    )
    gateway = LLMGateway(
        {
            "relevance_be": FakeBackend(provider="relevance_be", responses=[relevance]),
            "extract_be": extract_be,
        },
        policy=policy,
    )
    return gateway, extract_be


@pytest.fixture(autouse=True)
def _patch_get_raw_document(monkeypatch: pytest.MonkeyPatch) -> StoredRawDocument:
    """Patch ingestion.get_raw_document (called by fetch_document) to a seeded row."""
    doc = _stored_doc()

    async def _fake_get(session: object, document_id: uuid.UUID) -> StoredRawDocument:
        return doc

    monkeypatch.setattr(
        "civicsignals_api.modules.extraction.pipeline.ingestion_services.get_raw_document",
        _fake_get,
    )
    return doc


async def test_pipeline_relevant_produces_candidates(
    _patch_get_raw_document: StoredRawDocument,
) -> None:
    doc = _patch_get_raw_document
    gw, _extract_be = _gateway(
        relevance=json.dumps({"relevant": True, "categories": ["procurement"], "confidence": 0.9}),
        extract=json.dumps(
            {
                "candidates": [
                    {"signal_type": "rfp_posted", "confidence": 0.8, "fields": {"title": "ERP RFP"}}
                ]
            }
        ),
    )
    classifier = RelevanceClassifier(gateway=gw)
    session = _FakeSession()
    storage = _FakeStorage(b"RFP for ERP modernization, due 2026-06-01")
    job_id = uuid.uuid4()

    result = await pipeline.run_extraction_pipeline(
        session,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
        job_id=job_id,
        raw_document_id=doc.id,
        classifier=classifier,
        gateway=gw,
        prefilter="classifier",
    )

    assert result.skipped is False
    assert result.relevant is True
    assert len(result.candidates) == 1
    assert result.candidates[0].signal_type == "rfp_posted"
    # A relevance decision row + one candidate row were added.
    candidate_rows = [r for r in session.added if isinstance(r, ExtractionCandidate)]
    assert len(candidate_rows) == 1
    assert candidate_rows[0].job_id == job_id
    assert candidate_rows[0].raw_document_id == doc.id
    assert candidate_rows[0].dedup_key == "rfp_posted:erp rfp"


async def test_pipeline_irrelevant_skips_extract(
    _patch_get_raw_document: StoredRawDocument,
) -> None:
    doc = _patch_get_raw_document
    # Extract backend has NO scripted response: if extract were called it would
    # echo the prompt (and produce a fallback candidate). We assert it is never
    # reached by checking no candidate rows are stored and skipped=True.
    gw, extract_be = _gateway(
        relevance=json.dumps({"relevant": False, "categories": [], "confidence": 0.95}),
        extract="SHOULD NOT BE CALLED",
    )
    classifier = RelevanceClassifier(gateway=gw)
    session = _FakeSession()
    storage = _FakeStorage(b"employee recognitions and ceremonial proclamations")

    result = await pipeline.run_extraction_pipeline(
        session,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
        job_id=uuid.uuid4(),
        raw_document_id=doc.id,
        classifier=classifier,
        gateway=gw,
        prefilter="classifier",
    )

    assert result.skipped is True
    assert result.relevant is False
    assert result.candidates == []
    # No candidate rows stored — the gate dropped the doc before extraction.
    assert [r for r in session.added if isinstance(r, ExtractionCandidate)] == []
    # The extract backend was never invoked (the cost lever, doc 19 §3.1).
    assert extract_be.calls == []


async def test_pipeline_low_confidence_relevant_dropped(
    _patch_get_raw_document: StoredRawDocument,
) -> None:
    doc = _patch_get_raw_document
    gw, _extract_be = _gateway(
        relevance=json.dumps({"relevant": True, "categories": [], "confidence": 0.4}),
        extract="SHOULD NOT BE CALLED",
    )
    session = _FakeSession()
    result = await pipeline.run_extraction_pipeline(
        session,  # type: ignore[arg-type]
        _FakeStorage(b"maybe relevant"),  # type: ignore[arg-type]
        job_id=uuid.uuid4(),
        raw_document_id=doc.id,
        classifier=RelevanceClassifier(gateway=gw),
        gateway=gw,
        prefilter="classifier",
    )
    # Below the grey-zone floor -> dropped (doc 19 §3.2), treated as skipped.
    assert result.skipped is True
    assert [r for r in session.added if isinstance(r, ExtractionCandidate)] == []


async def test_pipeline_document_not_found_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(session: object, document_id: uuid.UUID) -> None:
        return None

    # Overrides the autouse fixture's patch (a later setattr wins) so fetch finds
    # nothing and the pipeline surfaces a hard DocumentNotFoundError.
    monkeypatch.setattr(
        "civicsignals_api.modules.extraction.pipeline.ingestion_services.get_raw_document",
        _none,
    )
    with pytest.raises(pipeline.DocumentNotFoundError):
        await pipeline.run_extraction_pipeline(
            _FakeSession(),  # type: ignore[arg-type]
            _FakeStorage(b""),  # type: ignore[arg-type]
            job_id=uuid.uuid4(),
            raw_document_id=uuid.uuid4(),
            prefilter="classifier",
        )
