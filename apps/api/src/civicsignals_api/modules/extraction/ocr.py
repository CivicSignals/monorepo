"""OCR backends for the extraction pipeline (doc 19 §2.2; E9).

When ``pdfplumber`` returns fewer than 200 characters for a PDF with more than 5
pages, the document is almost certainly scanned/image-only. This module supplies
the pluggable OCR layer that the parse stage falls back to in that case.

Three backends are provided:

- :class:`TesseractBackend` — self-host default. Uses ``pytesseract`` + ``pdf2image``
  (lazy imports, both in the ``extraction`` optional dep group) to rasterise each PDF
  page with Poppler, then run Tesseract OCR. Free but slow (~30s/page on CPU).
- :class:`TextractBackend` — cloud paid tier. Calls AWS Textract
  ``detect_document_text`` via ``boto3`` (already a core dep, lazy-called here).
  ~$0.0015/page, ~5s round-trip. Config-gated (``OCR_BACKEND=textract``).
- :class:`FakeBackend` — deterministic, no I/O. Returns canned text per page for
  use in tests. Never raises; selected in test fixtures only.

**Page cap** (doc 19 §2.2): OCR is capped at **100 pages** per document.  For PDFs
longer than 100 pages, only the **first 50 + last 25** pages are processed (skipping
the middle) and ``ocr_truncated`` is set on the result.

Backend selection is controlled by the ``OCR_BACKEND`` environment variable (default
``tesseract``, or the value injected by the caller — production passes it through the
:class:`OcrConfig` helper so the setting is queryable without importing env in the
parse path).

Usage:
    backend = get_ocr_backend()        # reads OCR_BACKEND env var
    result = backend.run(pdf_bytes)
"""

from __future__ import annotations

import io
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Final

import structlog

log = structlog.get_logger(__name__)

# Page-cap constants (doc 19 §2.2).
OCR_PAGE_HARD_CAP: Final = 100  # OCR at most this many pages per doc
OCR_FIRST_PAGES: Final = 50  # take the first N pages for long docs
OCR_LAST_PAGES: Final = 25  # …plus the last M pages


@dataclass(frozen=True, slots=True)
class OcrResult:
    """Output of one OCR run over a PDF.

    ``text`` is the concatenated OCR text (paragraph-joined, normalised by the
    caller).  ``ocr_used`` is always ``True`` for a real backend run (the parse
    stage sets it on the :class:`~.schemas.ParsedDocument`).
    ``ocr_truncated`` is ``True`` when the document exceeded the page cap and the
    pipeline only processed the first-50 + last-25 pages (doc 19 §2.2).
    ``backend`` names the backend that produced this result for logging/provenance.
    """

    text: str
    ocr_used: bool = True
    ocr_truncated: bool = False
    backend: str = "unknown"
    pages_processed: int = 0


class OcrBackend(ABC):
    """Abstract OCR backend.  Implementations are registered by name in
    :func:`get_ocr_backend` and selected via the ``OCR_BACKEND`` env var.
    """

    @abstractmethod
    def run(self, pdf_bytes: bytes) -> OcrResult:
        """Run OCR over the PDF bytes and return an :class:`OcrResult`.

        Must be synchronous (the parse stage runs in a thread context inside the
        Celery worker). Implementations **must not raise** — a failed OCR run should
        return whatever partial text was gathered and set a short reason in the text
        (the parse stage wraps the whole call in a try/except too, but best-effort
        means the backend itself shouldn't blow up the job).
        """


# ---------------------------------------------------------------------------
# FakeBackend (tests / CI — no real OCR)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FakeOcrBackend(OcrBackend):
    """Deterministic OCR backend for tests.

    Returns ``page_text`` repeated once per page (or ``default_text`` when
    ``pages`` is 0, meaning the page count isn't known until runtime — the
    backend receives bytes it doesn't inspect).  Always succeeds; never
    imports ``pytesseract`` or ``boto3``.
    """

    page_text: str = "OCR extracted text for page {n}."
    # Simulate a fixed total page count (for testing truncation logic).
    simulated_page_count: int = 1

    def run(self, pdf_bytes: bytes) -> OcrResult:
        del pdf_bytes  # unused by design — fake backend ignores the raw bytes
        total = self.simulated_page_count
        pages_to_ocr = _select_pages(total)
        truncated = len(pages_to_ocr) < total
        parts: list[str] = []
        for n in pages_to_ocr:
            parts.append(self.page_text.format(n=n))
        return OcrResult(
            text="\n\n".join(parts),
            ocr_used=True,
            ocr_truncated=truncated,
            backend="fake",
            pages_processed=len(pages_to_ocr),
        )


# ---------------------------------------------------------------------------
# TesseractBackend (self-host default)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TesseractBackend(OcrBackend):
    """OCR via ``pytesseract`` + ``pdf2image`` (doc 19 §2.2 self-host path).

    Both dependencies are lazy-imported from the ``extraction`` optional extra
    (not installed in the api image).  Poppler must be present on the system
    (installed in the worker Docker image) for pdf2image to rasterise pages.
    Falls back gracefully when imports fail.

    Typical throughput: ~30 s per page on 2 vCPUs.
    """

    dpi: int = 150  # lower DPI = faster; board-packet text is usually large-font
    lang: str = "eng"

    def run(self, pdf_bytes: bytes) -> OcrResult:
        try:
            from pdf2image import convert_from_bytes
        except ImportError:
            log.warning("extraction.ocr.pdf2image_unavailable")
            return OcrResult(text="", ocr_used=True, backend="tesseract", pages_processed=0)

        try:
            import pytesseract
        except ImportError:
            log.warning("extraction.ocr.pytesseract_unavailable")
            return OcrResult(text="", ocr_used=True, backend="tesseract", pages_processed=0)

        # Convert all pages to PIL images first to get the total page count.
        try:
            all_images = convert_from_bytes(pdf_bytes, dpi=self.dpi)
        except Exception as exc:
            log.warning("extraction.ocr.pdf2image_failed", error=str(exc))
            return OcrResult(text="", ocr_used=True, backend="tesseract", pages_processed=0)

        total = len(all_images)
        pages_to_ocr = _select_pages(total)
        truncated = len(pages_to_ocr) < total

        parts: list[str] = []
        for page_num in pages_to_ocr:
            img = all_images[page_num - 1]  # pages_to_ocr is 1-indexed
            try:
                page_text: str = pytesseract.image_to_string(img, lang=self.lang)
                if page_text.strip():
                    parts.append(page_text)
            except Exception as exc:
                log.warning(
                    "extraction.ocr.tesseract_page_failed",
                    page=page_num,
                    error=str(exc),
                )
                # Best-effort: skip this page and continue.

        return OcrResult(
            text="\n\n".join(parts),
            ocr_used=True,
            ocr_truncated=truncated,
            backend="tesseract",
            pages_processed=len(pages_to_ocr),
        )


# ---------------------------------------------------------------------------
# TextractBackend (cloud paid tier)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TextractBackend(OcrBackend):
    """OCR via AWS Textract ``detect_document_text`` (doc 19 §2.2 cloud tier).

    ``boto3`` is already a core dep so no extra import is needed. Each page is
    rasterised to a JPEG via ``pdf2image`` and sent to Textract as raw bytes.
    The per-page call is synchronous; the worker thread handles the I/O.

    Pricing: ~$0.0015/page, ~5s round-trip.
    Config-gated: enabled only when ``OCR_BACKEND=textract`` in the environment.
    """

    region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))
    dpi: int = 150

    def run(self, pdf_bytes: bytes) -> OcrResult:
        try:
            from pdf2image import convert_from_bytes
        except ImportError:
            log.warning("extraction.ocr.pdf2image_unavailable")
            return OcrResult(text="", ocr_used=True, backend="textract", pages_processed=0)

        try:
            all_images = convert_from_bytes(pdf_bytes, dpi=self.dpi)
        except Exception as exc:
            log.warning("extraction.ocr.pdf2image_failed", error=str(exc))
            return OcrResult(text="", ocr_used=True, backend="textract", pages_processed=0)

        import boto3  # already a core dep (boto3>=1.35 in pyproject.toml)

        client = boto3.client("textract", region_name=self.region)
        total = len(all_images)
        pages_to_ocr = _select_pages(total)
        truncated = len(pages_to_ocr) < total

        parts: list[str] = []
        for page_num in pages_to_ocr:
            img = all_images[page_num - 1]
            buf = io.BytesIO()
            img.save(buf, format="JPEG")
            jpeg_bytes = buf.getvalue()
            try:
                response: dict[str, object] = client.detect_document_text(
                    Document={"Bytes": jpeg_bytes}
                )
                blocks: list[dict[str, object]] = response.get("Blocks", [])  # type: ignore[assignment]
                page_lines = [
                    b.get("Text", "")
                    for b in blocks
                    if b.get("BlockType") == "LINE" and b.get("Text")
                ]
                page_text = "\n".join(page_lines)  # type: ignore[arg-type]
                if page_text.strip():
                    parts.append(page_text)
            except Exception as exc:
                log.warning(
                    "extraction.ocr.textract_page_failed",
                    page=page_num,
                    error=str(exc),
                )

        return OcrResult(
            text="\n\n".join(parts),
            ocr_used=True,
            ocr_truncated=truncated,
            backend="textract",
            pages_processed=len(pages_to_ocr),
        )


# ---------------------------------------------------------------------------
# Page selection (first-50 + last-25 for long docs, doc 19 §2.2)
# ---------------------------------------------------------------------------


def _select_pages(total: int) -> list[int]:
    """Return the 1-indexed page numbers to OCR for a document with ``total`` pages.

    - ``total`` <= 100: all pages (no truncation).
    - ``total`` > 100: first 50 + last 25 pages, deduplicated and sorted.
      This preserves the executive-summary / cover section (front) and the
      contract/resolution text (back) of long board packets.
    """
    if total <= OCR_PAGE_HARD_CAP:
        return list(range(1, total + 1))

    first = list(range(1, OCR_FIRST_PAGES + 1))
    last_start = max(OCR_FIRST_PAGES + 1, total - OCR_LAST_PAGES + 1)
    last = list(range(last_start, total + 1))
    seen: set[int] = set()
    result: list[int] = []
    for p in first + last:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


# ---------------------------------------------------------------------------
# Backend registry + factory
# ---------------------------------------------------------------------------

_BACKEND_REGISTRY: dict[str, type[OcrBackend]] = {
    "tesseract": TesseractBackend,
    "textract": TextractBackend,
    "fake": FakeOcrBackend,
}


def get_ocr_backend(name: str | None = None) -> OcrBackend:
    """Return an :class:`OcrBackend` instance for the given name.

    ``name`` defaults to the ``OCR_BACKEND`` environment variable, which
    defaults to ``"tesseract"`` when unset (the self-host default per doc 19 §2.2).

    Raises :class:`ValueError` for an unknown backend name so a misconfigured
    deployment fails loudly at startup rather than silently falling back.
    """
    backend_name = name or os.environ.get("OCR_BACKEND", "tesseract")
    cls = _BACKEND_REGISTRY.get(backend_name)
    if cls is None:
        raise ValueError(
            f"Unknown OCR backend {backend_name!r}. "
            f"Valid options: {sorted(_BACKEND_REGISTRY)}"
        )
    return cls()
