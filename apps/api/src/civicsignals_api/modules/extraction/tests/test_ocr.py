"""Tests for the E9 OCR pipeline (doc 19 §2.2).

Tests cover:
- OCR trigger condition: <200 chars AND >5 pages → OCR invoked.
- No OCR when chars ≥ 200 (even for long PDFs).
- No OCR when ≤ 5 pages (even for very short text).
- Page-cap truncation: first-50 + last-25 for >100-page docs + ``ocr_truncated``.
- ``ocr_used`` flag set when OCR ran.
- Backend selection via ``get_ocr_backend``.
- OCR failure is non-fatal (parse degrades, job continues).
- FakeOcrBackend determinism.
- ``_select_pages`` helper correctness.
- ``parse_document`` + ``run_extraction_pipeline`` integration with FakeOcrBackend.
"""

from __future__ import annotations

import uuid

import pytest

from civicsignals_api.modules.extraction import pipeline
from civicsignals_api.modules.extraction.ocr import (
    FakeOcrBackend,
    OcrResult,
    TesseractBackend,
    TextractBackend,
    _select_pages,
    get_ocr_backend,
)
from civicsignals_api.modules.extraction.pipeline import (
    OCR_MIN_PDF_CHARS,
    OCR_MIN_PDF_PAGES,
    parse_document,
)
from civicsignals_api.modules.ingestion.services import StoredRawDocument

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stored_doc(*, content_type: str = "application/pdf") -> StoredRawDocument:
    return StoredRawDocument(
        id=uuid.uuid4(),
        recipe_id="board_packet_test",
        recipe_version=1,
        connector="http_static",
        source_url="https://example.gov/board-packet.pdf",
        fetched_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        content_hash="0" * 64,
        blob_key="sha256/" + "0" * 64,
        content_type=content_type,
    )


# ---------------------------------------------------------------------------
# _select_pages helper
# ---------------------------------------------------------------------------


def test_select_pages_small_doc() -> None:
    """Docs ≤ 100 pages: all pages, no truncation."""
    pages = _select_pages(10)
    assert pages == list(range(1, 11))


def test_select_pages_exact_cap() -> None:
    """Docs at exactly 100 pages: all pages."""
    pages = _select_pages(100)
    assert pages == list(range(1, 101))
    assert len(pages) == 100


def test_select_pages_over_cap_structure() -> None:
    """Docs > 100 pages: first 50 + last 25, sorted, deduped."""
    pages = _select_pages(200)
    assert len(pages) == 75  # 50 + 25, no overlap (last starts at 176)
    assert pages[:50] == list(range(1, 51))
    assert pages[50:] == list(range(176, 201))


def test_select_pages_over_cap_no_duplicates() -> None:
    """Dedup when last-25 block would overlap with first-50."""
    # With 70 pages: first 50 = 1-50; last 25 starts at max(51, 70-25+1=46) = 51.
    pages = _select_pages(70)
    assert sorted(pages) == list(set(pages))  # no duplicates


def test_select_pages_101_pages() -> None:
    """Minimal over-cap case: 101 pages."""
    pages = _select_pages(101)
    # First 50 + last 25 = pages 1-50 and 77-101
    assert 1 in pages
    assert 50 in pages
    assert 77 in pages
    assert 101 in pages
    assert 51 not in pages


# ---------------------------------------------------------------------------
# FakeOcrBackend
# ---------------------------------------------------------------------------


def test_fake_backend_small_doc() -> None:
    fake = FakeOcrBackend(page_text="page {n} text", simulated_page_count=3)
    result = fake.run(b"dummy")
    assert result.ocr_used is True
    assert result.ocr_truncated is False
    assert result.backend == "fake"
    assert result.pages_processed == 3
    assert "page 1 text" in result.text


def test_fake_backend_large_doc_truncates() -> None:
    fake = FakeOcrBackend(page_text="p{n}", simulated_page_count=150)
    result = fake.run(b"dummy")
    assert result.ocr_truncated is True
    assert result.pages_processed == 75  # first 50 + last 25
    # First page present, a middle page absent.
    assert "p1" in result.text
    assert "p51" not in result.text  # page 51 is in the skipped middle


def test_fake_backend_exact_cap_no_truncation() -> None:
    fake = FakeOcrBackend(page_text="pg{n}", simulated_page_count=100)
    result = fake.run(b"dummy")
    assert result.ocr_truncated is False
    assert result.pages_processed == 100


# ---------------------------------------------------------------------------
# get_ocr_backend
# ---------------------------------------------------------------------------


def test_get_ocr_backend_default_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCR_BACKEND", raising=False)
    backend = get_ocr_backend()
    assert isinstance(backend, TesseractBackend)


def test_get_ocr_backend_tesseract_explicit() -> None:
    backend = get_ocr_backend("tesseract")
    assert isinstance(backend, TesseractBackend)


def test_get_ocr_backend_textract() -> None:
    backend = get_ocr_backend("textract")
    assert isinstance(backend, TextractBackend)


def test_get_ocr_backend_fake() -> None:
    backend = get_ocr_backend("fake")
    assert isinstance(backend, FakeOcrBackend)


def test_get_ocr_backend_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown OCR backend"):
        get_ocr_backend("nonexistent_backend")


def test_get_ocr_backend_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "fake")
    backend = get_ocr_backend()
    assert isinstance(backend, FakeOcrBackend)


# ---------------------------------------------------------------------------
# parse_document + OCR trigger
# ---------------------------------------------------------------------------


def _make_parse_pdf_stub(
    text: str,
    page_count: int,
) -> object:
    """Return a monkeypatched ``_parse_pdf`` that returns ``(text, page_count)``."""

    def _stub(content: bytes) -> tuple[str, int]:
        del content  # unused by stub — returns canned values
        return text, page_count

    return _stub


def test_parse_document_ocr_triggered_on_short_long_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """<200 chars AND >5 pages → OCR backend is invoked, ocr_used=True."""
    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text="short", page_count=10)
    )
    fake = FakeOcrBackend(page_text="real OCR text for page {n}", simulated_page_count=10)
    doc = _stored_doc()
    parsed = parse_document(doc, b"%PDF-1.4 stub", ocr_backend=fake)

    assert parsed.ocr_used is True
    assert parsed.ocr_truncated is False
    assert "real OCR text" in parsed.text


def test_parse_document_no_ocr_when_text_sufficient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """≥200 chars → OCR NOT triggered even for a long PDF."""
    long_text = "A" * OCR_MIN_PDF_CHARS  # exactly at threshold (not below)
    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text=long_text, page_count=50)
    )
    fake = FakeOcrBackend(page_text="should not appear", simulated_page_count=50)
    doc = _stored_doc()
    parsed = parse_document(doc, b"%PDF-1.4 stub", ocr_backend=fake)

    assert parsed.ocr_used is False
    assert "should not appear" not in parsed.text
    assert parsed.text == long_text  # normalised, but the content is from pdfplumber


def test_parse_document_no_ocr_for_short_pdf_few_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """<200 chars BUT ≤5 pages → no OCR (just flagged degraded per spec)."""
    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text="", page_count=3)
    )
    doc = _stored_doc()
    parsed = parse_document(doc, b"%PDF-1.4 stub")

    assert parsed.ocr_used is False
    assert parsed.degraded is True  # flagged degraded, not OCR'd


def test_parse_document_ocr_truncated_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OCR over a >100-page doc sets ocr_truncated=True."""
    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text="x", page_count=150)
    )
    fake = FakeOcrBackend(page_text="pg{n}", simulated_page_count=150)
    doc = _stored_doc()
    parsed = parse_document(doc, b"%PDF-1.4 stub", ocr_backend=fake)

    assert parsed.ocr_used is True
    assert parsed.ocr_truncated is True


def test_parse_document_ocr_not_truncated_at_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly 100 pages: OCR runs all pages, ocr_truncated=False."""
    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text="", page_count=100)
    )
    fake = FakeOcrBackend(page_text="pg{n}", simulated_page_count=100)
    doc = _stored_doc()
    parsed = parse_document(doc, b"%PDF-1.4 stub", ocr_backend=fake)

    assert parsed.ocr_used is True
    assert parsed.ocr_truncated is False


def test_parse_document_ocr_failure_is_non_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OCR backend raising an exception → parse returns whatever text exists + ocr_used."""

    class BrokenBackend(FakeOcrBackend):
        def run(self, pdf_bytes: bytes) -> OcrResult:
            raise RuntimeError("Tesseract not installed")

    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text="", page_count=10)
    )
    doc = _stored_doc()
    # Should not raise — the parse stage is non-fatal.
    parsed = parse_document(doc, b"%PDF-1.4 stub", ocr_backend=BrokenBackend())

    assert parsed.ocr_used is True  # OCR was attempted
    assert parsed.ocr_truncated is False
    # Whatever text pdfplumber returned (empty here) is kept.
    assert parsed.text == ""


def test_parse_document_non_pdf_no_ocr() -> None:
    """Non-PDF content types never trigger OCR."""
    doc = _stored_doc(content_type="text/html")
    fake = FakeOcrBackend(page_text="SHOULD NOT APPEAR", simulated_page_count=10)
    parsed = parse_document(
        doc, b"<html><body><p>RFP for ERP</p></body></html>", ocr_backend=fake
    )
    assert parsed.ocr_used is False
    assert "SHOULD NOT APPEAR" not in parsed.text


def test_parse_document_ocr_char_count_updated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After OCR, char_count reflects the OCR text length."""
    ocr_text = "OCR page content " * 20  # 340 chars
    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text="", page_count=10)
    )
    fake = FakeOcrBackend(
        page_text=ocr_text,
        simulated_page_count=10,
    )
    doc = _stored_doc()
    parsed = parse_document(doc, b"%PDF-1.4 stub", ocr_backend=fake)

    assert parsed.char_count == len(parsed.text)
    assert parsed.char_count > 0


# ---------------------------------------------------------------------------
# Boundary: exactly at the trigger threshold
# ---------------------------------------------------------------------------


def test_parse_document_ocr_boundary_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """199 chars (< 200) + 6 pages (> 5) → OCR triggered."""
    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text="A" * 199, page_count=6)
    )
    fake = FakeOcrBackend(page_text="OCR text {n}", simulated_page_count=6)
    doc = _stored_doc()
    parsed = parse_document(doc, b"%PDF-1.4 stub", ocr_backend=fake)
    assert parsed.ocr_used is True


def test_parse_document_no_ocr_exactly_at_page_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """5 pages (= OCR_MIN_PDF_PAGES, not >) → no OCR, just flagged degraded."""
    monkeypatch.setattr(
        pipeline, "_parse_pdf", _make_parse_pdf_stub(text="", page_count=OCR_MIN_PDF_PAGES)
    )
    doc = _stored_doc()
    parsed = parse_document(doc, b"%PDF-1.4 stub")
    assert parsed.ocr_used is False
    assert parsed.degraded is True
