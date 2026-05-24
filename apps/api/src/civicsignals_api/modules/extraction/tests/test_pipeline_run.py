"""Orchestrator tests for ``run_extraction_pipeline`` (doc 19 §1; E1).

These drive the full fetch -> parse -> relevance -> extract -> score -> dedupe ->
store chain without a real database, using:

- a ``FakeBackend`` gateway (relevance + extract calls, no network),
- a fake :class:`RawDocumentStorage` returning seeded bytes,
- a fake ingestion ``get_raw_document`` (monkeypatched) returning a seeded row,
- a minimal fake ``AsyncSession`` that records ``add()``\\ed rows,
- a stubbed ``signals.services.promote_candidate_to_signal`` (the store stage's
  E4 promotion needs real Postgres upsert semantics, exercised in
  ``test_pipeline_persistence.py``); the stub records the candidates it received so
  the orchestrator wiring can be asserted DB-free.

The relevance-gate-drop path (no extract call) and the persistence shape are the
key behaviours. The live-Postgres round-trip + real candidate→signal promotion is
in ``test_pipeline_persistence.py``.
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
    FakeEmbeddingBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.extraction import pipeline
from civicsignals_api.modules.extraction.models import ExtractionCandidate
from civicsignals_api.modules.extraction.relevance import RelevanceClassifier
from civicsignals_api.modules.ingestion.services import StoredRawDocument


class _FakeSessionTransaction:
    """Async context manager mirroring ``AsyncSession.begin()`` — commits on exit
    (rolls back if the block raised), recording the call on the owning session.
    """

    def __init__(self, session: _FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeSessionTransaction:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is None:
            await self._session.commit()
        else:
            await self._session.rollback()
        return False


class _FakeSession:
    """A minimal stand-in for AsyncSession recording added rows (no DB).

    ``run_extraction_pipeline`` calls ``add`` + ``flush`` (the relevance-decision
    record and the candidate rows) and manages its own transaction boundaries via
    ``commit`` / ``rollback`` / ``begin`` — under PgBouncer transaction-mode pooling
    it must never hold a transaction across an LLM call (doc 06 §4). The transaction
    methods are no-ops here (no real DB); ``flush`` assigns ids so the rows look
    persisted. We do not exercise ``get`` / ``execute``.
    """

    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "__table__"):
                obj.id = uuid.uuid4()  # type: ignore[attr-defined]

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    def begin(self) -> _FakeSessionTransaction:
        return _FakeSessionTransaction(self)


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

    E11: extract_candidates now runs two-pass entity extraction before signal
    detection, both routed to ``TASK_EXTRACTION``. The extract backend is
    seeded with two responses: a minimal entity extraction reply (consumed by
    entity extraction Pass 1) then the test's ``extract`` signal reply.
    """
    _entity_reply = json.dumps(
        {
            "organizations": [{"name": "Seattle Public Schools", "confidence": 0.85}],
            "persons": [{"name": "Alice", "role": "CIO", "confidence": 0.8}],
            "monetary_amounts": [{"amount_cents": 5000000, "currency": "USD", "confidence": 0.9}],
            "dates": [],
            "products_categories": [],
            "vendors_mentioned": [],
            "contract_terms_mentions": [],
            "raw_keywords": [],
            "extraction_confidence": 0.82,
            "extraction_warnings": [],
        }
    )
    extract_be = FakeBackend(provider="extract_be", responses=[_entity_reply, extract])
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
        # The store stage embeds each promoted signal (I1); a deterministic fake
        # embedding backend keeps that step network-free.
        embedding_backends={"fake_embed": FakeEmbeddingBackend(provider="fake_embed")},
        embedding_choice=ModelChoice("fake_embed", "fake-embed-model"),
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


class _FakeSignal:
    """A stand-in promoted signal; the store stage only reads its ``id`` (I1 embed)."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()


@pytest.fixture
def _stub_promote(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Stub the E4 promotion + I1 embed (both need real Postgres; see persistence test).

    Records the :class:`CandidateInput` instances the store stage builds so the
    orchestrator wiring can be asserted without a database. Returns a fake signal
    object (with an ``id``) so ``store_candidates`` proceeds as on a successful
    promote. The embed step is stubbed to a no-op here — its DB round-trip is
    exercised in ``test_pipeline_persistence.py``.
    """
    received: list[object] = []

    async def _fake_promote(session: object, candidate: object) -> object:
        received.append(candidate)
        return _FakeSignal()

    async def _fake_embed(session: object, signal_ids: list[uuid.UUID], **kwargs: object) -> int:
        return len(signal_ids)

    monkeypatch.setattr(
        "civicsignals_api.modules.extraction.pipeline.signals_services.promote_candidate_to_signal",
        _fake_promote,
    )
    monkeypatch.setattr(
        "civicsignals_api.modules.extraction.pipeline.signals_services.embed_signals",
        _fake_embed,
    )
    return received


async def test_pipeline_relevant_produces_candidates(
    _patch_get_raw_document: StoredRawDocument,
    _stub_promote: list[object],
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
    # The dedupe stage stamps the canonical per-type hash (doc 19 §7.1; E5).
    assert candidate_rows[0].dedup_key is not None
    assert len(candidate_rows[0].dedup_key) == 64
    # The candidate row was stamped promoted, and the store stage handed exactly one
    # CandidateInput to the signals promotion service (E4).
    assert candidate_rows[0].status == pipeline.CANDIDATE_STATUS_PROMOTED
    assert len(_stub_promote) == 1
    promoted = _stub_promote[0]
    assert promoted.signal_type == "rfp_posted"  # type: ignore[attr-defined]
    assert promoted.extraction_job_id == job_id  # type: ignore[attr-defined]
    assert promoted.source_candidate_id == candidate_rows[0].id  # type: ignore[attr-defined]


async def test_pipeline_does_not_hold_txn_across_llm(
    _patch_get_raw_document: StoredRawDocument,
    _stub_promote: list[object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard for the PgBouncer transaction-mode pooling fix (doc 06 §4).

    A DB transaction must never be held across an LLM call. The relevance gate must
    be invoked with ``session=None`` (so its LLM call holds no transaction), and the
    document read must be committed (the pooled connection released) before it runs.
    """
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

    captured: dict[str, object] = {}
    real_gate = pipeline.run_relevance_gate

    async def _spy_gate(*args: object, **kwargs: object) -> object:
        captured["relevance_session"] = kwargs.get("session")
        captured["commits_at_relevance"] = session.commits
        return await real_gate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline, "run_relevance_gate", _spy_gate)

    await pipeline.run_extraction_pipeline(
        session,  # type: ignore[arg-type]
        storage,  # type: ignore[arg-type]
        job_id=uuid.uuid4(),
        raw_document_id=doc.id,
        classifier=classifier,
        gateway=gw,
        prefilter="classifier",
    )

    # The relevance LLM ran with no session (so it held no transaction) ...
    assert captured["relevance_session"] is None
    # ... and the document read was committed (connection released) before it.
    assert isinstance(captured["commits_at_relevance"], int)
    assert captured["commits_at_relevance"] >= 1


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
