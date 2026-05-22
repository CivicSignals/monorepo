"""Unit tests for the individual extraction pipeline stages (doc 19 §2-§7; E1).

These exercise the stage functions in isolation — parse (text/html/pdf-fallback),
extract (FakeBackend gateway), score, and dedupe — with no database. The full
fetch->...->store orchestration that needs persistence is in
``test_pipeline_run.py`` (a fake session) and ``test_pipeline_persistence.py``
(a live Postgres, gated on ``EXTRACTION_TEST_DSN``).
"""

from __future__ import annotations

import json
import uuid

import pytest

from civicsignals_api.llm_gateway import (
    TASK_EXTRACTION,
    FakeBackend,
    LLMGateway,
    ModelChoice,
    TaskModelPolicy,
)
from civicsignals_api.modules.extraction import pipeline
from civicsignals_api.modules.extraction.schemas import CandidateRecord, ParsedDocument
from civicsignals_api.modules.ingestion.services import StoredRawDocument


def _stored_doc(*, content_type: str | None = "text/plain") -> StoredRawDocument:
    return StoredRawDocument(
        id=uuid.uuid4(),
        recipe_id="seattle_school_board_agendas",
        recipe_version=1,
        connector="http_static",
        source_url="https://example.gov/agenda",
        fetched_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        content_hash="0" * 64,
        blob_key="sha256/" + "0" * 64,
        content_type=content_type,
    )


def _extract_gateway(*responses: str) -> LLMGateway:
    backend = FakeBackend(responses=list(responses))
    policy = TaskModelPolicy(overrides={TASK_EXTRACTION: ModelChoice("fake", "sonnet")})
    return LLMGateway({"fake": backend}, policy=policy)


# --- parse ----------------------------------------------------------------


def test_parse_plain_text() -> None:
    doc = _stored_doc(content_type="text/plain")
    parsed = pipeline.parse_document(doc, b"RFP for ERP\n\n\n  modernization  ")
    assert isinstance(parsed, ParsedDocument)
    # Whitespace collapsed, paragraph break preserved.
    assert parsed.text == "RFP for ERP\n\nmodernization"
    assert parsed.degraded is False
    assert parsed.char_count == len(parsed.text)
    assert parsed.recipe_id == doc.recipe_id


def test_parse_html_strips_tags_and_scripts() -> None:
    doc = _stored_doc(content_type="text/html")
    html = b"<html><head><style>x{}</style></head><body><h1>RFP</h1>"
    html += b"<script>evil()</script><p>ERP modernization</p></body></html>"
    parsed = pipeline.parse_document(doc, html)
    assert "RFP" in parsed.text
    assert "ERP modernization" in parsed.text
    assert "evil" not in parsed.text  # script content dropped
    assert "x{}" not in parsed.text  # style content dropped


def test_parse_unknown_content_type_is_degraded() -> None:
    doc = _stored_doc(content_type="application/octet-stream")
    parsed = pipeline.parse_document(doc, b"some bytes")
    assert parsed.degraded is True
    assert "some bytes" in parsed.text


def test_parse_pdf_below_ocr_threshold_is_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force the PDF text-layer extraction to return "" (independent of whether the
    # `extraction` extra / pdfplumber is installed), so the test deterministically
    # exercises the < OCR_MIN_PDF_CHARS path that flags degraded — the # TODO E9
    # OCR hook (doc 19 §2.2).
    monkeypatch.setattr(pipeline, "_parse_pdf", lambda content: "")
    doc = _stored_doc(content_type="application/pdf")
    parsed = pipeline.parse_document(doc, b"%PDF-1.4 ...")
    assert parsed.degraded is True


def test_parse_pdf_extracts_text(monkeypatch: pytest.MonkeyPatch) -> None:
    # When the text layer yields enough text, the parse is not degraded.
    monkeypatch.setattr(pipeline, "_parse_pdf", lambda content: "RFP for ERP " * 30)
    doc = _stored_doc(content_type="application/pdf")
    parsed = pipeline.parse_document(doc, b"%PDF-1.4 ...")
    assert parsed.degraded is False
    assert "RFP for ERP" in parsed.text


# --- extract --------------------------------------------------------------


async def test_extract_candidates_parses_llm_output() -> None:
    reply = json.dumps(
        {
            "candidates": [
                {
                    "signal_type": "rfp_posted",
                    "confidence": 0.9,
                    "fields": {"title": "ERP RFP", "summary": "modernization"},
                },
                {"signal_type": "budget_approved", "confidence": 0.7, "fields": {}},
            ]
        }
    )
    gw = _extract_gateway(reply)
    parsed = ParsedDocument(
        raw_document_id=uuid.uuid4(),
        recipe_id="seattle_school_board_agendas",
        text="RFP for ERP modernization, budget approved",
    )
    candidates = await pipeline.extract_candidates(gw, parsed, workspace_id=None)
    assert len(candidates) == 2
    assert candidates[0].signal_type == "rfp_posted"
    assert candidates[0].confidence == pytest.approx(0.9)
    assert candidates[0].extraction_method == pipeline.METHOD_LLM
    assert candidates[1].signal_type == "budget_approved"


async def test_extract_candidates_caps_confidence() -> None:
    reply = json.dumps({"candidates": [{"signal_type": "rfp_posted", "confidence": 1.0}]})
    gw = _extract_gateway(reply)
    parsed = ParsedDocument(raw_document_id=uuid.uuid4(), recipe_id="r", text="x")
    candidates = await pipeline.extract_candidates(gw, parsed, workspace_id=None)
    # 1.0 is suspicious; capped at 0.95 (doc 19 §6.4).
    assert candidates[0].confidence == pytest.approx(0.95)


async def test_extract_candidates_empty_list() -> None:
    gw = _extract_gateway(json.dumps({"candidates": []}))
    parsed = ParsedDocument(raw_document_id=uuid.uuid4(), recipe_id="r", text="x")
    candidates = await pipeline.extract_candidates(gw, parsed, workspace_id=None)
    assert candidates == []


async def test_extract_candidates_unparseable_falls_back() -> None:
    gw = _extract_gateway("this is not json at all")
    parsed = ParsedDocument(raw_document_id=uuid.uuid4(), recipe_id="r", text="x")
    candidates = await pipeline.extract_candidates(gw, parsed, workspace_id=None)
    # Fail soft: one degraded candidate carrying the raw output so nothing is lost.
    assert len(candidates) == 1
    assert candidates[0].extraction_method == pipeline.METHOD_FALLBACK
    assert candidates[0].fields["raw_output"] == "this is not json at all"


# --- score (E6 banded blend) + dedupe --------------------------------------


def test_score_candidate_blends_and_bands() -> None:
    # rfp_posted with a plausible due_at, model self-reported 0.8, neutral source
    # quality, 0/6 optional fields filled, entity unresolved. The blend:
    #   field_level 0.8*0.40 + llm 0.8*0.20 + source 0.5*0.15 +
    #   schema_completeness 0.0*0.15 + cross_validation 0.5*0.10 = 0.605
    # which lands in the degraded band (0.6-0.8, doc 19 §6.3).
    c = CandidateRecord(
        signal_type="rfp_posted",
        confidence=0.8,
        fields={"title": "ERP RFP", "summary": "x", "due_at": "2026-06-01T17:00:00Z"},
    )
    out = pipeline.score_candidate(c)
    assert out.confidence == pytest.approx(0.605)
    assert out.band == "degraded"
    assert pipeline.candidate_is_rejected(out) is False


def test_score_candidate_unknown_type_bands_self_report() -> None:
    # No usable signal type: keep the self-reported confidence and band it (the E4
    # strict gate rejects it at store regardless).
    c = CandidateRecord(signal_type=None, confidence=0.9, fields={})
    out = pipeline.score_candidate(c)
    assert out.confidence == pytest.approx(0.9)
    assert out.band == "normal"


def test_score_candidate_unknown_type_defaults_neutral() -> None:
    c = CandidateRecord(signal_type=None, confidence=None, fields={})
    out = pipeline.score_candidate(c)
    assert out.confidence == pytest.approx(0.5)
    assert out.band == "pending_review"  # 0.5 -> pending_review band


def test_score_candidate_rejects_low_blend() -> None:
    # A leadership_change missing all optional fields, no model confidence, entity
    # unresolved -> a low blend that lands in the rejected band (< 0.4).
    c = CandidateRecord(
        signal_type="leadership_change",
        confidence=0.1,
        fields={"title": "x", "summary": "y", "role": "CIO", "person_name": "A. Doe"},
    )
    out = pipeline.score_candidate(c)
    assert out.band == "rejected"
    assert pipeline.candidate_is_rejected(out) is True


def test_score_candidate_respects_config_override() -> None:
    from civicsignals_api.modules.signals.services import (
        BandThresholds,
        ConfidenceConfig,
    )

    c = CandidateRecord(
        signal_type="rfp_posted",
        confidence=0.8,
        fields={"title": "ERP RFP", "summary": "x", "due_at": "2026-06-01T17:00:00Z"},
    )
    # A noisy aggregator lowers the floors: the same 0.605 blend is now "normal".
    cfg = ConfidenceConfig(
        thresholds=BandThresholds(normal_floor=0.6, degraded_floor=0.4, pending_review_floor=0.2)
    )
    out = pipeline.score_candidate(c, config=cfg)
    assert out.confidence == pytest.approx(0.605)
    assert out.band == "normal"


def test_dedupe_candidate_computes_canonical_key() -> None:
    """The dedupe stage stamps the canonical per-type hash (doc 19 §7.1; E5)."""
    from civicsignals_api.modules.signals import services as signals_services

    c = CandidateRecord(
        signal_type="rfp_posted",
        fields={"title": "ERP RFP", "due_at": "2026-06-01T17:00:00Z"},
    )
    out = pipeline.dedupe_candidate(c)
    # A SHA-256 hex digest of entity_id + signal_type + normalized key fields — equal
    # to what the signals service computes for the same fields (entity_id pending).
    assert out.dedup_key is not None
    assert len(out.dedup_key) == 64
    assert out.dedup_key == signals_services.dedupe_key_for_candidate(
        "rfp_posted", {"title": "ERP RFP", "due_at": "2026-06-01T17:00:00Z"}
    )


def test_dedupe_candidate_normalizes_title_case_and_whitespace() -> None:
    """Casing / whitespace differences hash identically (doc 19 §7.1, §7.3)."""
    a = pipeline.dedupe_candidate(
        CandidateRecord(
            signal_type="rfp_posted", fields={"title": "ERP RFP", "due_at": "2026-06-01"}
        )
    )
    b = pipeline.dedupe_candidate(
        CandidateRecord(
            signal_type="rfp_posted", fields={"title": "  erp   rfp ", "due_at": "2026-06-01"}
        )
    )
    assert a.dedup_key == b.dedup_key


def test_dedupe_candidate_unknown_type() -> None:
    c = CandidateRecord(signal_type=None, fields={})
    out = pipeline.dedupe_candidate(c)
    # Unknown/absent signal type -> no key here; the store path's validated-payload
    # hash takes over (E5).
    assert out.dedup_key is None


# --- verdict gate ---------------------------------------------------------


def test_verdict_passes_thresholds() -> None:
    from civicsignals_api.modules.extraction.schemas import RelevanceVerdict

    assert pipeline.verdict_passes(RelevanceVerdict(relevant=True, confidence=0.9)) is True
    assert pipeline.verdict_passes(RelevanceVerdict(relevant=True, confidence=0.6)) is True
    # Below the grey-zone floor -> dropped (doc 19 §3.2).
    assert pipeline.verdict_passes(RelevanceVerdict(relevant=True, confidence=0.5)) is False
    assert pipeline.verdict_passes(RelevanceVerdict(relevant=False, confidence=0.99)) is False
