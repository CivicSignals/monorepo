"""``pdf_extractor`` chain (doc 18 §1 cat. F, §5 wave 1 #3; D6).

Verifies the ordered primary → fallback chain: pdfplumber text → OCR → LLM-table,
each rung flagged on the result, and an unprocessable PDF raising. pdfplumber
itself is mocked (it ships in the ``extraction`` extra, not installed in CI), so
these run everywhere; the OCR + LLM rungs use injected fakes (the real OCR is the
E9 seam).
"""

from __future__ import annotations

import pytest

from civicsignals_api.modules.ingestion.connectors import pdf_extractor as pdf_module
from civicsignals_api.modules.ingestion.connectors.pdf_extractor import (
    PdfConfig,
    PdfExtractionError,
    PdfExtractionMethod,
    PdfExtractionUnavailableError,
    PdfExtractor,
    PdfExtractorConnector,
)
from civicsignals_api.modules.recipes import services as recipes_services

_PDF_BYTES = b"%PDF-1.7 fake bytes"


class _FakeOcr:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def image_pdf_to_text(self, pdf_bytes: bytes, *, max_pages: int | None) -> str:
        self.calls += 1
        return self.text


class _FakeLlm:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.calls = 0

    def extract_tables(self, pdf_bytes: bytes, *, hint: str) -> str | None:
        self.calls += 1
        return self.text


def _patch_pdfplumber(monkeypatch: pytest.MonkeyPatch, *, text: str, pages: int = 3) -> None:
    monkeypatch.setattr(pdf_module, "_pdfplumber_text", lambda b, *, max_pages: (text, pages))


def test_primary_text_layer_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pdfplumber(monkeypatch, text="Clean born-digital text", pages=5)
    result = PdfExtractor().extract(_PDF_BYTES)
    assert result.text == "Clean born-digital text"
    assert result.method is PdfExtractionMethod.TEXT
    assert result.page_count == 5
    assert result.degraded is False


def test_ocr_fallback_when_no_text_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pdfplumber(monkeypatch, text="")  # image-only PDF: no text layer
    ocr = _FakeOcr("text recovered via OCR")
    result = PdfExtractor(PdfConfig(ocr_fallback=True), ocr_engine=ocr).extract(_PDF_BYTES)
    assert result.text == "text recovered via OCR"
    assert result.method is PdfExtractionMethod.OCR
    assert result.degraded is True
    assert ocr.calls == 1


def test_llm_table_fallback_after_ocr_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pdfplumber(monkeypatch, text="")
    ocr = _FakeOcr("")  # OCR also whiffs
    llm = _FakeLlm("tables extracted by the LLM")
    extractor = PdfExtractor(
        PdfConfig(ocr_fallback=True, llm_table_fallback=True),
        ocr_engine=ocr,
        llm_extractor=llm,
    )
    result = extractor.extract(_PDF_BYTES)
    assert result.text == "tables extracted by the LLM"
    assert result.method is PdfExtractionMethod.LLM_TABLE
    assert result.degraded is True
    # Ordering: OCR tried before the LLM rung.
    assert ocr.calls == 1
    assert llm.calls == 1


def test_chain_order_text_short_circuits_ocr_and_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pdfplumber(monkeypatch, text="text layer present")
    ocr = _FakeOcr("should not be called")
    llm = _FakeLlm("should not be called")
    extractor = PdfExtractor(
        PdfConfig(ocr_fallback=True, llm_table_fallback=True),
        ocr_engine=ocr,
        llm_extractor=llm,
    )
    result = extractor.extract(_PDF_BYTES)
    assert result.method is PdfExtractionMethod.TEXT
    assert ocr.calls == 0  # primary won -> fallbacks never run
    assert llm.calls == 0


def test_unprocessable_pdf_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # No text, OCR off, LLM off -> nothing recovers text -> dead-letter.
    _patch_pdfplumber(monkeypatch, text="")
    with pytest.raises(PdfExtractionError):
        PdfExtractor(PdfConfig(ocr_fallback=False, llm_table_fallback=False)).extract(_PDF_BYTES)


def test_ocr_default_is_noop_until_e9(monkeypatch: pytest.MonkeyPatch) -> None:
    # With no OCR engine injected, the OCR rung is a no-op (TODO E9), so an
    # image-only PDF falls through to dead-letter rather than silently empty.
    _patch_pdfplumber(monkeypatch, text="")
    with pytest.raises(PdfExtractionError):
        PdfExtractor(PdfConfig(ocr_fallback=True, llm_table_fallback=False)).extract(_PDF_BYTES)


def test_corrupt_pdf_surfaces_as_extraction_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(b: bytes, *, max_pages: int | None) -> tuple[str, int]:
        raise PdfExtractionError("could not open PDF: corrupt")

    monkeypatch.setattr(pdf_module, "_pdfplumber_text", boom)
    with pytest.raises(PdfExtractionError, match="corrupt"):
        PdfExtractor().extract(_PDF_BYTES)


def test_missing_pdfplumber_raises_clear_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "pdfplumber":
            raise ImportError("No module named 'pdfplumber'")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(PdfExtractionUnavailableError):
        pdf_module._pdfplumber_text(_PDF_BYTES, max_pages=None)


def test_connector_registered_and_exposes_extractor() -> None:
    recipe = recipes_services.parse_recipe(
        {
            "recipe_id": "minutes-pdf",
            "connector": "pdf_extractor",
            "version": 1,
            "entity": {"name": "Some District"},
            "connector_config": {"pdf_extractor": {"ocr_fallback": True}},
        }
    )
    connector = PdfExtractorConnector(recipe)
    assert isinstance(connector.extractor(), PdfExtractor)


def test_real_pdfplumber_on_image_only_pdf() -> None:
    # Smoke against the real library when the extraction extra is installed: a
    # trivial single-page PDF with no text layer extracts empty (-> OCR territory).
    pdfplumber = pytest.importorskip("pdfplumber")
    # A minimal valid one-page PDF with no text content.
    minimal_pdf = (
        b"%PDF-1.1\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>\n"
    )
    text, pages = pdf_module._pdfplumber_text(minimal_pdf, max_pages=None)
    assert isinstance(pdfplumber.__version__, str)
    assert text == ""  # no text layer
    assert pages == 1
