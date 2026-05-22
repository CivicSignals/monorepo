"""``pdf_extractor`` — PDF text extraction chain (doc 18 §1 cat. F, §5 wave 1 #3; D6).

Content reached as a PDF (budgets, board minutes, strategic plans, FOIA
responses). This connector is **consumed by other connectors, not scheduled
directly** (doc 18 §1, §5): when ``http_static`` / ``bulk_download`` fetch a
``.pdf`` URL, they hand the bytes here to recover text.

The extraction follows the spec's ordered primary → fallback chain (doc 18 §2.3,
§4 cat. F), each rung flagged on the result so drift/cost monitoring sees which
fired:

1. **pdfplumber** text layer (the primary). Clean, born-digital PDFs stop here.
2. **OCR fallback** when there's no text layer (image-only / scanned PDF). The
   OCR engine (Tesseract MVP) lands in **E9** — wired here behind a seam and
   marked ``# TODO E9`` so the chain ordering is in place now.
3. **LLM-assisted table extraction** via the shared LLM gateway when tables parse
   poorly — the safety net (doc 18 §4 "Tables extract poorly"). The prompt/registry
   wiring is ``# TODO`` (E3); the seam routes through the gateway, never a vendor
   SDK directly (doc 06 §7).
4. Otherwise the document is **unprocessable** (doc 18 §4 "Password-protected" /
   "Corrupt PDF") and the caller dead-letters it.

pdfplumber ships in the **extraction optional extra**, so it is imported lazily —
the lean ``api`` image never needs it. Exercising the PDF path without the extra
raises a clear :class:`PdfExtractionUnavailableError`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar, Protocol

from pydantic import BaseModel, ConfigDict

from civicsignals_api.modules.recipes.services import Fetcher

from .base import Connector, ConnectorError, register
from .http_static import HttpStaticConfig, HttpxFetcher


class PdfExtractionUnavailableError(ConnectorError):
    """pdfplumber (the ``extraction`` extra) is not installed."""

    def __init__(self, cause: ImportError | None = None) -> None:
        super().__init__(
            "the pdf_extractor connector requires pdfplumber, which ships in the "
            "'extraction' optional extra. Install it: `uv sync --extra extraction`. "
            "Only worker_extract carries this extra; the lean api image does not."
        )
        self.__cause__ = cause


class PdfExtractionError(ConnectorError):
    """A PDF could not be turned into text by any rung of the chain.

    The caller (the connector that fetched the PDF) dead-letters the document.
    Distinguishes a genuinely unprocessable PDF (corrupt / password-protected /
    empty after every fallback) from a missing-dependency error.
    """


class PdfExtractionMethod(StrEnum):
    """Which rung of the chain produced the text (doc 18 §2.3, §4 cat. F)."""

    TEXT = "pdf_text"  # pdfplumber text layer (primary)
    OCR = "ocr"  # OCR fallback (image-only PDF)
    LLM_TABLE = "llm_table"  # LLM-assisted table extraction (safety net)


class PdfExtractionResult(BaseModel):
    """Outcome of the PDF chain: the recovered text + which rung produced it."""

    model_config = ConfigDict(extra="forbid")

    text: str
    method: PdfExtractionMethod
    page_count: int
    #: True when a non-primary rung produced the text (degraded, mirrors doc 18 §3.4).
    degraded: bool = False


class PdfConfig(BaseModel):
    """Per-recipe ``connector_config.pdf_extractor`` (mirrors the JSON Schema)."""

    model_config = ConfigDict(extra="forbid")

    ocr_fallback: bool = True
    llm_table_fallback: bool = False
    max_pages: int | None = None


class OcrEngine(Protocol):
    """Seam for the OCR fallback (TODO E9). The default raises until E9 lands.

    Keeping it a Protocol lets the chain ordering be tested now (inject a fake
    that returns text) and lets E9 drop in Tesseract/Textract without touching
    this module's control flow.
    """

    def image_pdf_to_text(self, pdf_bytes: bytes, *, max_pages: int | None) -> str: ...


class LlmTableExtractor(Protocol):
    """Seam for the LLM-assisted table-extraction safety net (doc 18 §4).

    Routes through ``civicsignals_api.llm_gateway`` (doc 06 §7); the default
    implementation wires the gateway, tests inject a deterministic fake.
    """

    def extract_tables(self, pdf_bytes: bytes, *, hint: str) -> str | None: ...


def _pdfplumber_text(pdf_bytes: bytes, *, max_pages: int | None) -> tuple[str, int]:
    """Extract the text layer with pdfplumber. Returns ``(text, page_count)``.

    Empty/whitespace-only text means no usable text layer (image-only PDF) — the
    caller falls through to OCR. Raises :class:`PdfExtractionError` on a corrupt /
    password-protected PDF (doc 18 §4) and :class:`PdfExtractionUnavailableError`
    when the extra is missing.
    """
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - exercised via the guard test
        raise PdfExtractionUnavailableError(exc) from exc

    import io

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
            page_count = len(pdf.pages)
            parts = [page.extract_text() or "" for page in pages]
    except PdfExtractionUnavailableError:
        raise
    except Exception as exc:  # pdfplumber raises a grab-bag; treat as unprocessable
        raise PdfExtractionError(f"could not open PDF: {exc}") from exc
    return "\n\n".join(parts).strip(), page_count


class PdfExtractor:
    """Run the ordered PDF extraction chain (doc 18 §2.3, §4 cat. F).

    Construct with optional OCR / LLM seams (defaults are the production wirings,
    OCR being a stub until E9). :meth:`extract` walks pdfplumber → OCR → LLM and
    returns a :class:`PdfExtractionResult`, or raises :class:`PdfExtractionError`
    when nothing recovered text.
    """

    def __init__(
        self,
        config: PdfConfig | None = None,
        *,
        ocr_engine: OcrEngine | None = None,
        llm_extractor: LlmTableExtractor | None = None,
    ) -> None:
        self._config = config or PdfConfig()
        self._ocr_engine = ocr_engine
        self._llm_extractor = llm_extractor

    def extract(self, pdf_bytes: bytes) -> PdfExtractionResult:
        max_pages = self._config.max_pages

        # 1. Primary: pdfplumber text layer.
        text, page_count = _pdfplumber_text(pdf_bytes, max_pages=max_pages)
        if text:
            return PdfExtractionResult(
                text=text, method=PdfExtractionMethod.TEXT, page_count=page_count
            )

        # 2. Fallback: OCR for image-only PDFs (no text layer). Stub until E9.
        if self._config.ocr_fallback:
            ocr_text = self._ocr(pdf_bytes, max_pages=max_pages)
            if ocr_text:
                return PdfExtractionResult(
                    text=ocr_text,
                    method=PdfExtractionMethod.OCR,
                    page_count=page_count,
                    degraded=True,
                )

        # 3. Safety net: LLM-assisted table extraction.
        if self._config.llm_table_fallback:
            llm_text = self._llm(pdf_bytes)
            if llm_text:
                return PdfExtractionResult(
                    text=llm_text,
                    method=PdfExtractionMethod.LLM_TABLE,
                    page_count=page_count,
                    degraded=True,
                )

        # 4. Nothing recovered text -> unprocessable; caller dead-letters.
        raise PdfExtractionError(
            "PDF has no extractable text after pdfplumber"
            f"{', OCR' if self._config.ocr_fallback else ''}"
            f"{', and LLM-table' if self._config.llm_table_fallback else ''} fallbacks "
            "(image-only/corrupt/password-protected — doc 18 §4 cat. F)"
        )

    def _ocr(self, pdf_bytes: bytes, *, max_pages: int | None) -> str | None:
        engine = self._ocr_engine
        if engine is None:
            # TODO E9: wire the Tesseract (MVP) / Textract (cloud) OCR engine here.
            # Until E9 lands the OCR rung is a no-op (returns None -> fall through),
            # so an image-only PDF dead-letters with a clear reason rather than
            # silently producing empty text.
            return None
        return engine.image_pdf_to_text(pdf_bytes, max_pages=max_pages) or None

    def _llm(self, pdf_bytes: bytes) -> str | None:
        extractor = self._llm_extractor
        if extractor is None:
            extractor = _GatewayTableExtractor()
        return extractor.extract_tables(
            pdf_bytes, hint="Extract the tables from this document as text."
        )


class _GatewayTableExtractor:
    """Default :class:`LlmTableExtractor`: routes through the shared LLM gateway.

    # TODO: replace the inline prompt with the versioned prompt registry (E3) and
    # render the PDF's page text/images rather than a placeholder. The seam exists
    # now so the chain ordering is real; a missing/disabled backend surfaces as
    # ``None`` (the document dead-letters) rather than crashing the run.
    """

    def extract_tables(self, pdf_bytes: bytes, *, hint: str) -> str | None:
        # Lazy import: only worker_extract carries the gateway's deps.
        import asyncio

        from civicsignals_api.llm_gateway import TASK_EXTRACTION, LLMError, get_gateway

        gateway = get_gateway()
        try:
            # TODO E9/E3: pass the rendered page text/images, not just a hint.
            result = asyncio.run(
                gateway.complete(
                    prompt=hint,
                    task=TASK_EXTRACTION,
                    max_tokens=1024,
                )
            )
        except LLMError:
            return None
        value = result.text.strip()
        return value or None


@register
class PdfExtractorConnector(Connector):
    """PDF connector (doc 18 §1 cat. F). Not scheduled directly — consumed by
    other connectors that fetch a ``.pdf`` URL and call :class:`PdfExtractor`.

    It still exposes a static fetcher so a recipe *can* point ``pdf_extractor`` at
    a single PDF URL for testing; the real value is :class:`PdfExtractor`.
    """

    name: ClassVar[str] = "pdf_extractor"
    config_model: ClassVar[type[BaseModel] | None] = PdfConfig

    def build_fetcher(self) -> Fetcher:
        return HttpxFetcher(HttpStaticConfig())

    def extractor(self) -> PdfExtractor:
        """Build the :class:`PdfExtractor` configured by this recipe."""
        config = self.config
        assert isinstance(config, PdfConfig)
        return PdfExtractor(config)


__all__ = [
    "LlmTableExtractor",
    "OcrEngine",
    "PdfConfig",
    "PdfExtractionError",
    "PdfExtractionMethod",
    "PdfExtractionResult",
    "PdfExtractionUnavailableError",
    "PdfExtractor",
    "PdfExtractorConnector",
]
