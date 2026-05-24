"""The extraction pipeline stages (doc 19 §2-§7; E1).

This module is the orchestration core: small, individually-testable stage
functions plus :func:`run_extraction_pipeline`, which wires them in funnel order
(doc 19 §1):

    fetch -> parse -> relevance gate -> extract -> score -> dedupe -> store

Each stage is a plain (async where it needs I/O) function so it can be tested in
isolation. The Celery task in ``tasks.py`` is the thin process boundary: it builds
the session + storage + gateway, calls :func:`run_extraction_pipeline`, and owns
retry/dead-letter on failure (doc 19 §12.1).

The stages deliberately use the *gateway* (never a vendor SDK — doc 06 §7), D3
content-addressable storage (``ingestion.get_raw_document`` + ``storage``), and
the E8 :class:`RelevanceClassifier`. Several stages are intentional stubs with a
clear seam:

- **parse**: handles text/html/pdf; OCR fallback (E9) for short PDFs (doc 19 §2.2).
  When ``pdfplumber`` yields < 200 chars for a PDF with > 5 pages, the pluggable OCR
  backend (Tesseract/Textract/Fake, doc 19 §2.2) is invoked. The result carries
  ``ocr_used`` and (for > 100 page docs) ``ocr_truncated`` flags.
- **extract**: emits permissive :class:`CandidateRecord` dicts; the **strict typed
  per-signal-type schema + required-field hard gate** (doc 19 §6.1; E4) runs in the
  store stage when a candidate is promoted into a signal.
- **score**: the E6 banded confidence blend (doc 19 §6.2-§6.3) — assigns each
  candidate a blended confidence + band; a ``rejected``-band candidate is dropped
  before store.
- **dedupe**: stamps the candidate with the canonical per-type dedupe key via the
  signals service (doc 19 §7.1; E5). The windowed lookup + merge (doc 19 §7.2-§7.3)
  needs the DB session, so it runs in the store path; the embedding-based fuzzy
  fallback (doc 19 §7.4) is E10/I1.
- **store**: persists candidates to ``extraction_candidate`` **and** promotes each
  validated candidate into a global ``signals_signal`` row via
  ``signals.services.promote_candidate_to_signal`` (E4), which now dedups within the
  type window and merges corroborating documents into a surviving signal (doc 19 §7;
  E5). A candidate that fails the strict schema gate raises and dead-letters the job
  with the surfaced validation error (doc 19 §6.1) — the candidate row is still
  persisted (rejected, for the audit) but no signal is written.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.llm_gateway import TASK_EXTRACTION, LLMGateway, get_gateway
from civicsignals_api.modules.ingestion import services as ingestion_services
from civicsignals_api.modules.ingestion.services import RawDocumentStorage, StoredRawDocument
from civicsignals_api.modules.signals import services as signals_services
from civicsignals_api.modules.signals.services import (
    DEFAULT_CONFIG,
    CandidateInput,
    ConfidenceBand,
    ConfidenceConfig,
    SignalType,
    SignalValidationError,
    score_candidate_confidence,
)

from .entity_extraction import run_entity_extraction
from .models import ExtractionCandidate
from .ocr import OcrBackend, get_ocr_backend
from .relevance import RelevanceClassifier
from .schemas import (
    CandidateRecord,
    DocumentRef,
    EntityExtractionResult,
    ExtractedEntity,
    ParsedDocument,
    RelevanceVerdict,
)

log = structlog.get_logger(__name__)

# Grey-zone threshold (doc 19 §3.2): a "relevant" verdict below this confidence is
# the maybe band. We proceed when relevant *and* at/above this floor; the 10%
# sampling of the sub-threshold band (doc 19 §3.2) is a TODO E11 refinement —
# until then a low-confidence relevant verdict is dropped (conservative on cost).
RELEVANCE_CONFIDENCE_FLOOR: Final = 0.6

# Stage-1 OCR trigger thresholds (doc 19 §2.2; E9).
# A PDF yielding < OCR_MIN_PDF_CHARS chars AND having > OCR_MIN_PDF_PAGES pages
# is almost certainly scanned/image-only — trigger the OCR fallback.
OCR_MIN_PDF_CHARS: Final = 200
OCR_MIN_PDF_PAGES: Final = 5

# Prompt identity for the extract stage. TODO E3/E4: the per-signal-type prompts
# (doc 19 §5.3) supersede this single permissive prompt; pinned here so candidates
# already carry reproducible provenance.
EXTRACT_PROMPT_NAME: Final = "signal_extraction"
EXTRACT_PROMPT_VERSION: Final = "v1"

# Truncation budget for the extract call (doc 19 §5.4 keeps the Sonnet pass bounded).
MAX_EXTRACT_CHARS: Final = 24_000

# Candidate provenance methods recorded on the row (doc 19 §4.1).
METHOD_LLM: Final = "llm"
METHOD_FALLBACK: Final = "fallback"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of one pipeline run, for the task/job-tracking layer.

    ``relevant`` is the gate verdict; ``candidates`` are the stored records (empty
    when the doc was dropped at the gate or no signal was found). ``skipped`` is
    True when the relevance gate dropped the document (a *success*, not a failure —
    doc 19 §3.2). ``signal_ids`` are the ids of the ``signals_signal`` rows the
    promoted candidates landed in (deduped, first-seen order) — the F3 scoring
    trigger reads these to enqueue per-signal scoring after the job commits.
    """

    raw_document_id: uuid.UUID
    relevant: bool
    skipped: bool
    candidates: list[CandidateRecord]
    signal_ids: list[uuid.UUID]


# ---------------------------------------------------------------------------
# Stage 1a: fetch (load the stored bytes via D3)
# ---------------------------------------------------------------------------


async def fetch_document(
    session: AsyncSession,
    storage: RawDocumentStorage,
    raw_document_id: uuid.UUID,
) -> tuple[StoredRawDocument, bytes]:
    """Load a stored raw document's provenance row + bytes (doc 18 §3.6; D3).

    Reads the ``ingestion_raw_document`` row through ingestion's service seam (no
    cross-module model import — doc 06 §3) and pulls the bytes back from S3 by its
    content-addressed ``blob_key``. Raises if the document or its blob is missing —
    a genuinely absent document is a hard error the task surfaces.
    """
    doc = await ingestion_services.get_raw_document(session, raw_document_id)
    if doc is None:
        raise DocumentNotFoundError(raw_document_id)
    content = storage.get_document(doc.blob_key)
    return doc, content


class DocumentNotFoundError(Exception):
    """The raw document id has no ``ingestion_raw_document`` row (a hard failure)."""

    def __init__(self, raw_document_id: uuid.UUID) -> None:
        super().__init__(f"no ingestion_raw_document for id {raw_document_id}")
        self.raw_document_id = raw_document_id


# ---------------------------------------------------------------------------
# Stage 1b: parse (bytes -> clean text)
# ---------------------------------------------------------------------------


def parse_document(
    doc: StoredRawDocument,
    content: bytes,
    *,
    ocr_backend: OcrBackend | None = None,
) -> ParsedDocument:
    """Extract clean text from the raw bytes (doc 19 §2.1-§2.2; E9).

    Dispatches on ``content_type``: PDF -> ``pdfplumber`` (lazy-imported; it lives
    in the ``extraction`` extra so the api image never pays for it), HTML ->
    BeautifulSoup main-text, JSON/XML/text -> decoded text. Anything unrecognised
    falls back to a best-effort UTF-8 decode and is flagged ``degraded``.

    **OCR fallback (E9, doc 19 §2.2):** when pdfplumber yields < 200 chars for a PDF
    with > 5 pages, the parse invokes the OCR backend (Tesseract by default; Textract
    on the cloud tier; Fake for tests). The result carries ``ocr_used=True`` and, for
    documents longer than 100 pages where only the first-50 + last-25 pages were
    OCR'd, ``ocr_truncated=True``. OCR failure is non-fatal — the parse degrades to
    whatever text exists rather than losing the document.

    ``ocr_backend`` may be injected for tests (overrides the ``OCR_BACKEND`` env var).
    When ``None``, :func:`~.ocr.get_ocr_backend` is called lazily (only when the OCR
    trigger fires) so processes that never process scanned PDFs pay zero import cost.

    Whitespace is collapsed to paragraph boundaries (doc 19 §2.4). Returns a
    :class:`ParsedDocument`; never raises on empty text (a relevance gate /
    downstream stage decides what an empty parse means).
    """
    content_type = (doc.content_type or "").lower()
    degraded = False
    ocr_used = False
    ocr_truncated = False

    if "pdf" in content_type:
        text, page_count = _parse_pdf(content)
        # E9 OCR decision (doc 19 §2.2): a PDF yielding < OCR_MIN_PDF_CHARS chars
        # AND having > OCR_MIN_PDF_PAGES pages is almost certainly scanned/image-only.
        if len(text) < OCR_MIN_PDF_CHARS and page_count > OCR_MIN_PDF_PAGES:
            log.info(
                "extraction.parse.pdf_ocr_triggered",
                raw_document_id=str(doc.id),
                chars=len(text),
                pages=page_count,
            )
            text, ocr_used, ocr_truncated = _run_ocr(
                content,
                ocr_backend=ocr_backend,
                doc_id=str(doc.id),
                fallback_text=text,
            )
        elif len(text) < OCR_MIN_PDF_CHARS:
            # Short PDF but ≤ 5 pages — too small to bother with OCR; flag degraded
            # so downstream can weight it lower (the doc may simply be near-empty).
            degraded = True
            log.info(
                "extraction.parse.pdf_short_no_ocr",
                raw_document_id=str(doc.id),
                chars=len(text),
                pages=page_count,
            )
    elif "html" in content_type:
        text = _parse_html(content)
    elif _is_texty(content_type):
        text = _decode(content)
    else:
        # Unknown content type: best-effort decode, flag degraded so downstream
        # can weight it lower (doc 19 §6.2 source-quality component).
        text = _decode(content)
        degraded = True

    text = _normalize_whitespace(text)
    return ParsedDocument(
        raw_document_id=doc.id,
        recipe_id=doc.recipe_id,
        source_url=doc.source_url,
        content_type=doc.content_type,
        text=text,
        char_count=len(text),
        degraded=degraded,
        ocr_used=ocr_used,
        ocr_truncated=ocr_truncated,
    )


def _run_ocr(
    content: bytes,
    *,
    ocr_backend: OcrBackend | None,
    doc_id: str,
    fallback_text: str = "",
) -> tuple[str, bool, bool]:
    """Invoke the OCR backend and return ``(text, ocr_used, ocr_truncated)``.

    Non-fatal: a backend failure (or a backend that returns empty text) falls back
    to ``fallback_text`` (the pdfplumber extract, which was < 200 chars but possibly
    non-empty) with ``ocr_used=True`` (the attempt was made) and ``ocr_truncated=False``.
    This ensures pdfplumber text is never silently discarded.
    """
    backend = ocr_backend if ocr_backend is not None else get_ocr_backend()
    try:
        result = backend.run(content)
        log.info(
            "extraction.parse.ocr_complete",
            doc_id=doc_id,
            backend=result.backend,
            pages_processed=result.pages_processed,
            ocr_truncated=result.ocr_truncated,
            chars=len(result.text),
        )
        # If the backend returned no text, preserve the original pdfplumber extract
        # rather than silently discarding whatever it found (doc 19 §2.2 best-effort).
        ocr_text = result.text if result.text.strip() else fallback_text
        return ocr_text, result.ocr_used, result.ocr_truncated
    except Exception as exc:
        # Best-effort: OCR failure must not lose the document (doc 19 §2.2 §12.1).
        # Include the stack trace so operators can diagnose backend issues.
        log.warning("extraction.parse.ocr_failed", doc_id=doc_id, error=str(exc), exc_info=True)
        return fallback_text, True, False


def _is_texty(content_type: str) -> bool:
    return any(t in content_type for t in ("text", "json", "xml"))


def _decode(content: bytes) -> str:
    """Decode bytes as UTF-8, replacing undecodable sequences (never raises)."""
    return content.decode("utf-8", errors="replace")


def _parse_html(content: bytes) -> str:
    """Extract readable text from HTML via BeautifulSoup (doc 19 §2.1 fallback).

    BeautifulSoup is in *core* deps (the recipe runner needs it), so this needs no
    extra. ``readability``/``lxml`` main-content extraction is the doc's primary
    path (``ingestion`` extra) — a TODO E11 refinement; the BeautifulSoup
    get-text path is the documented fallback and is dependency-free here.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(_decode(content), "html.parser")
    # Drop script/style noise before pulling text.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n")


def _parse_pdf(content: bytes) -> tuple[str, int]:
    """Extract the text layer from a PDF via ``pdfplumber`` (doc 19 §2.1).

    Returns ``(text, page_count)`` — the page count is needed by the OCR trigger
    (doc 19 §2.2; E9) to decide whether to fall back to OCR (> 5 pages + < 200 chars).

    ``pdfplumber`` lives in the ``extraction`` extra (installed on
    ``worker_extract``, not the api image), so it is imported lazily. If it is
    unavailable we degrade to empty text rather than crashing the import of any
    process that merely imports this module.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - depends on the extraction extra
        log.warning("extraction.parse.pdfplumber_unavailable")
        return "", 0

    import io

    # A malformed/encrypted/truncated PDF can make pdfplumber (pdfminer under it)
    # raise on open or per-page extraction. Parse is best-effort (doc 19 §2.1): a
    # bad PDF must degrade to empty text — the OCR hook re-attempts if the page
    # count warrants it — not crash the whole job.
    try:
        parts: list[str] = []
        page_count = 0
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text:
                    parts.append(page_text)
        return "\n\n".join(parts), page_count
    except Exception as exc:  # pdfminer raises a broad family of parse errors
        log.warning("extraction.parse.pdf_failed", error=str(exc))
        return "", 0


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace, preserving paragraph boundaries (doc 19 §2.4)."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    # Collapse runs of blank lines to a single paragraph break.
    out: list[str] = []
    blank = False
    for line in lines:
        if line:
            out.append(line)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# Stage 2: relevance gate (E8)
# ---------------------------------------------------------------------------


def _document_ref(parsed: ParsedDocument) -> DocumentRef:
    """Build the E8 relevance :class:`DocumentRef` from a parsed document."""
    return DocumentRef(
        raw_document_id=str(parsed.raw_document_id),
        recipe_id=parsed.recipe_id,
        source=parsed.source_url,
        text=parsed.text,
    )


async def run_relevance_gate(
    classifier: RelevanceClassifier,
    parsed: ParsedDocument,
    *,
    prefilter: str,
    workspace_id: str | None,
    session: AsyncSession | None,
) -> RelevanceVerdict:
    """Run the Stage-2 relevance gate over the parsed text (doc 19 §3; E8).

    Thin adapter: builds the E8 :class:`DocumentRef` from the parsed document and
    delegates to the injected classifier, which persists the decision when a
    ``session`` is given. The caller decides whether the verdict passes the funnel
    (see :func:`verdict_passes`). The pipeline passes ``session=None`` here so the
    relevance LLM call holds no open transaction, then records the decision via
    :func:`record_relevance_decision` alongside the candidate writes.
    """
    return await classifier.classify(
        _document_ref(parsed),
        prefilter=prefilter,
        workspace_id=workspace_id,
        session=session,
    )


async def record_relevance_decision(
    classifier: RelevanceClassifier,
    parsed: ParsedDocument,
    verdict: RelevanceVerdict,
    *,
    session: AsyncSession,
) -> None:
    """Persist a relevance verdict computed earlier with no DB session (E8).

    :func:`run_extraction_pipeline` runs the relevance LLM with ``session=None`` so
    the network call holds no transaction (PgBouncer transaction-mode pooling — doc
    06 §4), then records the decision here inside the same transaction that stores
    the candidates, so a retried run never half-writes or double-records it.
    """
    await classifier.record_decision(session, _document_ref(parsed), verdict)


def verdict_passes(verdict: RelevanceVerdict) -> bool:
    """Whether a gate verdict proceeds to extraction (doc 19 §3.2).

    Proceed when the document is relevant *and* the confidence is at/above the
    grey-zone floor. The 10% sampling of the sub-floor "maybe" band (doc 19 §3.2)
    is a TODO E11 refinement; until then a low-confidence relevant verdict is
    dropped, which is the cost-conservative default.
    """
    return verdict.relevant and verdict.confidence >= RELEVANCE_CONFIDENCE_FLOOR


# ---------------------------------------------------------------------------
# Stage 3/4: extract (LLM-assisted candidate detection)
# ---------------------------------------------------------------------------


async def extract_candidates(
    gateway: LLMGateway,
    parsed: ParsedDocument,
    *,
    workspace_id: str | None,
    session: AsyncSession | None = None,
) -> list[CandidateRecord]:
    """Emit candidate signal records from the document via the gateway (doc 19 §5).

    Routes through the LLM gateway (``TASK_EXTRACTION`` -> a Sonnet-class model)
    with the versioned ``signal_extraction`` prompt. Returns permissive
    :class:`CandidateRecord` objects.

    **E11 two-pass entity extraction (doc 19 §4):** before the signal-type
    detection call, the Stage-3 two-pass entity extraction runs:

    - Pass 1 (``entity_extraction/v2``, Haiku-class): fast candidate entity
      extraction — organisations, persons, amounts, dates, vendors, etc.
    - Pass 2 (``entity_extraction/v1``, Sonnet-class): triggered when Pass 1
      found fewer than 3 entity types or confidence < 0.6; refines and
      gap-fills the partial extraction.
    - Entity linking: looks up each organisation/vendor mention in the
      ``entities_entity`` table (via ``entities.services.search_entities``)
      and attaches a canonical ``entity_id`` when found.

    The ``EntityExtractionResult`` is attached to every emitted
    :class:`CandidateRecord` via ``entity_extraction``.  The first resolved
    entity's ``entity_name`` is also injected into ``fields["entity_name"]``
    (or the raw name when unresolved) so the downstream score/dedupe/store
    stages continue to work without modification (the ``entity_name`` field is
    already part of the ``signal_extraction`` prompt schema).

    If the entity extraction step fails entirely the signal-type detection still
    runs — entity data is best-effort (doc 19 §4.3 / §12.1).

    # TODO E4: the deterministic Stage-3 first pass (spaCy NER + regex, doc 19
    # §4.1) and the strict typed per-signal-type schemas + required-field hard gate
    # (doc 19 §5.1, §6.1) replace this single permissive LLM pass. The free-form
    # ``fields`` dict is the seam E4 reads from.
    """
    # --- Stage 3: two-pass entity extraction (E11, doc 19 §4) ----------------
    entity_result: EntityExtractionResult | None = None
    try:
        entity_result = await run_entity_extraction(
            gateway,
            parsed.text,
            workspace_id=workspace_id,
            session=session,
            raw_document_id=parsed.raw_document_id,
        )
        log.info(
            "extraction.entity_extraction.done",
            raw_document_id=str(parsed.raw_document_id),
            entities=len(entity_result.entities),
            method=entity_result.extraction_method,
            degraded=entity_result.degraded,
        )
    except Exception as exc:
        # Entity extraction is best-effort; a failure must not block signal
        # detection (doc 19 §12.1).
        log.warning(
            "extraction.entity_extraction.unexpected_failure",
            raw_document_id=str(parsed.raw_document_id),
            error=str(exc),
            exc_info=True,
        )

    # Release the entity-linking reads before the (network-bound) signal-type LLM
    # call: under PgBouncer transaction-mode pooling an open transaction pins a
    # pooled connection for the whole call (doc 06 §4). The linking above is
    # read-only, so committing here loses nothing. No-op when called session-less
    # (e.g. the stage unit tests, which pass no session and skip entity linking).
    if session is not None:
        await session.commit()

    # --- Stage 4: signal-type detection (signal_extraction prompt) -----------
    document_text, _truncated = _truncate(parsed.text, MAX_EXTRACT_CHARS)
    result = await gateway.complete(
        task=TASK_EXTRACTION,
        prompt_name=EXTRACT_PROMPT_NAME,
        prompt_version=EXTRACT_PROMPT_VERSION,
        prompt_vars={
            "source": parsed.source_url or parsed.recipe_id,
            "document": document_text,
        },
        max_tokens=2048,
        workspace_id=workspace_id,
    )

    candidates = _parse_candidates(result.text)
    if candidates is None:
        log.warning(
            "extraction.extract.unparseable_output",
            raw_document_id=str(parsed.raw_document_id),
            recipe_id=parsed.recipe_id,
            model=result.model,
        )
        # Fail soft: keep the raw model text as a single degraded candidate so the
        # extraction is not silently lost; E4 can re-run against the snapshot.
        return [
            CandidateRecord(
                signal_type=None,
                fields={"raw_output": result.text},
                extraction_method=METHOD_FALLBACK,
                entity_extraction=entity_result,
            )
        ]

    # Attach entity extraction result + propagate the best entity name into
    # fields["entity_name"] for downstream compatibility (doc 19 §4.3).
    enriched: list[CandidateRecord] = []
    for candidate in candidates:
        candidate = candidate.model_copy(update={"entity_extraction": entity_result})
        # Propagate a resolved (or raw) entity name into fields if not already set.
        if entity_result is not None and "entity_name" not in candidate.fields:
            best_entity = _best_entity(entity_result)
            if best_entity is not None:
                candidate = candidate.model_copy(
                    update={
                        "fields": {
                            **candidate.fields,
                            "entity_name": best_entity.entity_name or best_entity.raw_name,
                        }
                    }
                )
        enriched.append(candidate)
    return enriched


def _best_entity(entity_result: EntityExtractionResult) -> ExtractedEntity | None:
    """Return the highest-confidence resolved entity, or the first unresolved one."""
    resolved = [e for e in entity_result.entities if not e.resolution_pending]
    if resolved:
        return max(resolved, key=lambda e: e.confidence)
    if entity_result.entities:
        return max(entity_result.entities, key=lambda e: e.confidence)
    return None


def _parse_candidates(text: str) -> list[CandidateRecord] | None:
    """Parse the extract model reply into candidates, tolerating surrounding prose.

    Returns ``None`` on any parse failure (the caller emits a degraded fallback).
    Expects ``{"candidates": [...]}``; also accepts a bare list for robustness.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        # Maybe a bare JSON array.
        return _parse_candidate_list(text)
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, dict):
        return None
    raw = parsed.get("candidates")
    if not isinstance(raw, list):
        return None
    return _coerce_candidates(raw)


def _parse_candidate_list(text: str) -> list[CandidateRecord] | None:
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return _coerce_candidates(parsed) if isinstance(parsed, list) else None


def _coerce_candidates(raw: list[object]) -> list[CandidateRecord]:
    out: list[CandidateRecord] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        signal_type = item.get("signal_type")
        fields = item.get("fields")
        confidence = item.get("confidence")
        out.append(
            CandidateRecord(
                signal_type=signal_type if isinstance(signal_type, str) else None,
                fields=fields if isinstance(fields, dict) else {},
                confidence=_clamp_confidence(confidence),
                extraction_method=METHOD_LLM,
            )
        )
    return out


def _clamp_confidence(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # Cap at 0.95 — perfect certainty is suspicious (doc 19 §6.4).
    return max(0.0, min(0.95, value))


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip(), True


# ---------------------------------------------------------------------------
# Stage 5: score (confidence) — banded weighted blend (E6)
# ---------------------------------------------------------------------------


def score_candidate(
    candidate: CandidateRecord,
    *,
    config: ConfidenceConfig = DEFAULT_CONFIG,
    entity_resolved: bool = False,
) -> CandidateRecord:
    """Assign the banded extraction-confidence score (doc 19 §6.2-§6.3; E6).

    Runs the weighted blend (field-level confidences 40%, LLM self-report 20%,
    source quality 15%, schema completeness 15%, cross-validation 10% — doc 19 §6.2)
    over the candidate's typed ``signal_type`` + ``fields``, and maps the score to a
    band (doc 19 §6.3). The blended ``confidence`` and the ``band`` are written back
    onto the candidate; the store step lifts the band onto the signal row, and the
    orchestrator drops a ``rejected``-band candidate before store (doc 19 §6.3).

    ``config`` is the recipe-configurable scoring config (thresholds + source-quality
    tier + weights, doc 19 §6.3); ``entity_resolved`` feeds the cross-validation
    component (doc 19 §6.2). When the candidate's ``signal_type`` is unknown/absent
    (a malformed extract — the E4 strict gate will reject it at store anyway), the
    schema-completeness component cannot be computed, so we fall back to carrying the
    self-reported confidence and band it against the configured thresholds.
    """
    signal_type = _coerce_signal_type(candidate.signal_type)
    if signal_type is None:
        # Cannot run the typed blend without a known type. Keep the self-reported
        # confidence (defaulting neutral) and band it; the strict gate rejects it at
        # store regardless, so this only governs whether we bother promoting it.
        confidence = (
            candidate.confidence if candidate.confidence is not None else NEUTRAL_CONFIDENCE
        )
        band = config.thresholds.band_for(confidence)
        return candidate.model_copy(update={"confidence": confidence, "band": band.value})

    result = score_candidate_confidence(
        signal_type,
        candidate.fields,
        llm_confidence=candidate.confidence,
        entity_resolved=entity_resolved,
        config=config,
    )
    return candidate.model_copy(update={"confidence": result.score, "band": result.band.value})


# Neutral confidence used when a candidate has no usable signal type and the model
# reported nothing (mirrors ``signals.scoring.NEUTRAL``).
NEUTRAL_CONFIDENCE: Final = 0.5


def _coerce_signal_type(raw: str | None) -> SignalType | None:
    """Map a candidate's coarse ``signal_type`` string to the typed enum, or None."""
    if raw is None:
        return None
    try:
        return SignalType(raw)
    except ValueError:
        return None


def candidate_is_rejected(candidate: CandidateRecord) -> bool:
    """Whether a scored candidate fell in the ``rejected`` band (doc 19 §6.3).

    A ``rejected`` candidate is **not surfaced**: the orchestrator drops it before
    the store step (it is still logged via the dropped-count, doc 19 §6.3 "logged
    for retrospective analysis"). Used by :func:`run_extraction_pipeline` to filter
    the scored candidates.
    """
    return candidate.band == ConfidenceBand.REJECTED.value


# ---------------------------------------------------------------------------
# Stage 6: dedupe — canonical per-type key (E5)
# ---------------------------------------------------------------------------


def dedupe_candidate(candidate: CandidateRecord) -> CandidateRecord:
    """Compute the canonical per-type dedup key for the candidate (doc 19 §7.1; E5).

    Stage 6 (doc 19 §7): stamp the candidate with the canonical per-type dedupe hash
    (``entity_id + signal_type + normalized_key_fields``) via the signals service
    seam (``signals.services.dedupe_key_for_candidate`` — never ``signals.dedupe``
    directly; doc 06 §3). The windowed lookup + merge (doc 19 §7.2-§7.3) needs the
    DB session, so it runs in the store path (``signals.services.store_signal``,
    which recomputes the authoritative hash from the *validated* payload). Stamping
    it here keeps the staged ``extraction_candidate.dedup_key`` aligned with the
    signal's ``content_hash`` for the audit trail and gives the store path a key even
    before validation.

    ``entity_id`` is resolution-pending at this stage (doc 19 §4.3 / E10 not wired),
    so the hash is computed with ``entity_id=None`` here; the store path recomputes
    with the resolved id once E10 supplies one. An unknown/absent ``signal_type``
    yields no key (the store path's validated-payload hash takes over).

    # TODO E10/I1: embedding-based **fuzzy** dedupe (doc 19 §7.4) for high-stakes
    # types; this is the exact-key half only.
    """
    if candidate.dedup_key is not None:
        return candidate
    fields = candidate.fields if isinstance(candidate.fields, dict) else {}
    key = signals_services.dedupe_key_for_candidate(candidate.signal_type, fields)
    if key is None:
        return candidate
    return candidate.model_copy(update={"dedup_key": key})


# ---------------------------------------------------------------------------
# Stage 7: store (persist candidates)
# ---------------------------------------------------------------------------


# Status the candidate row carries once it is promoted / rejected (mirrors
# ``extraction_candidate.status``; doc 19 §5.1, §6.1).
CANDIDATE_STATUS_PROMOTED: Final = "promoted"
CANDIDATE_STATUS_REJECTED: Final = "rejected"


def _candidate_content_hash(candidate: CandidateRecord) -> str:
    """Provisional ``content_hash`` carried into the store path (doc 19 §7.1; E5).

    The dedupe stage (:func:`dedupe_candidate`) stamps ``dedup_key`` with the
    canonical per-type hash (already a SHA-256 hex), so we pass it straight through.
    The store path (``signals.services.store_signal``) is the **authority**: it
    recomputes the hash from the *validated* payload and runs the windowed merge
    (doc 19 §7.2-§7.3), so this value is only the provisional ``CandidateInput``
    seed. When the dedupe stage produced no key (unknown signal type), fall back to
    a stable digest of the type + fields so the seed is still populated.
    """
    basis = candidate.dedup_key
    if not basis:
        payload = json.dumps(
            {"type": candidate.signal_type, "fields": candidate.fields},
            sort_keys=True,
            default=str,
        )
        basis = payload
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


async def store_candidates(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    raw_document_id: uuid.UUID,
    recipe_id: str,
    candidates: list[CandidateRecord],
    ocr_used: bool = False,
    ocr_truncated: bool = False,
) -> tuple[list[ExtractionCandidate], list[uuid.UUID]]:
    """Persist candidates + promote validated ones into ``signals_signal`` (E1/E4).

    Two responsibilities (doc 19 §1 "store", §6.1 "validate"):

    1. Persist each candidate to ``extraction_candidate`` — the staging row E5 reads
       (the funnel stays lossless; nothing is dropped silently).
    2. Run the **strict per-type schema hard gate** (E4) and promote each valid
       candidate into a global ``signals_signal`` row via
       ``signals.services.promote_candidate_to_signal``. The candidate row is
       stamped ``promoted`` (with the resulting signal's id) on success.

    Returns the persisted candidate rows **and** the ids of the signals they were
    promoted into (deduped, first-seen order) — the embed step (I1) reads the latter
    to embed the freshly-stored signals.

    On a schema-validation failure the candidate row is stamped ``rejected`` (for
    the doc 19 §6.1 audit) and a :class:`SignalValidationError` is re-raised so the
    task dead-letters the job with the surfaced error — **no signal is written for a
    candidate that fails the gate**. The caller owns the transaction (this flushes,
    not commits), so on the raised error nothing is committed and the raw snapshot
    stays replayable.

    Promotion dedups within the type-specific window and merges corroborating
    documents into a surviving signal (doc 19 §7; E5) — the same RFP seen on the
    portal, a newspaper and the entity's own site collapses to one signal.

    ``ocr_used`` / ``ocr_truncated`` are the E9 OCR flags from the parse stage
    (doc 19 §2.2); both candidates from the same document share the same values and
    are recorded on each candidate row for the audit trail.

    # TODO E6: the real confidence blend feeds ``confidence`` (doc 19 §6.2).
    """
    rows: list[ExtractionCandidate] = []
    for candidate in candidates:
        row = ExtractionCandidate(
            job_id=job_id,
            raw_document_id=raw_document_id,
            recipe_id=recipe_id,
            signal_type=candidate.signal_type,
            fields=dict(candidate.fields),
            confidence=candidate.confidence,
            dedup_key=candidate.dedup_key,
            extraction_method=candidate.extraction_method,
            ocr_used=ocr_used,
            ocr_truncated=ocr_truncated,
        )
        session.add(row)
        rows.append(row)
    await session.flush()  # assign candidate ids before promotion links to them

    signal_ids: list[uuid.UUID] = []
    seen_signal_ids: set[uuid.UUID] = set()
    for candidate, row in zip(candidates, rows, strict=True):
        candidate_input = CandidateInput(
            signal_type=candidate.signal_type,
            # ``entity_name`` is a *convenience* key the extract stage injects into
            # fields (doc 19 §4.3) for downstream compatibility — it is lifted onto
            # ``CandidateInput.entity_name`` below. The strict per-type payload
            # schemas (E4) declare no ``entity_name`` field and ``forbid`` extras, so
            # it must not reach the validator, or a resolved-entity candidate would be
            # spuriously rejected. Strip it here (the only place it would otherwise
            # flow into the hard gate).
            fields=_fields_for_payload(candidate.fields),
            recipe_id=recipe_id,
            raw_document_id=raw_document_id,
            content_hash=_candidate_content_hash(candidate),
            # E11 (doc 19 §4.3): use the entity_id from the two-pass entity
            # extraction when a canonical entity was resolved; fall back to
            # None (resolution-pending) otherwise, as before E11 / E10.
            entity_id=_candidate_resolved_entity_id(candidate),
            entity_name=_candidate_entity_name(candidate),
            confidence=candidate.confidence,
            # The E6 band the score stage computed (doc 19 §6.3) drives the row's
            # status/degraded/review flags. A ``rejected``-band candidate never
            # reaches here — the orchestrator filters it before store.
            band=_coerce_band(candidate.band),
            extraction_job_id=job_id,
            source_candidate_id=row.id,
        )
        try:
            signal = await signals_services.promote_candidate_to_signal(session, candidate_input)
        except SignalValidationError:
            # Hard gate (doc 19 §6.1): record the rejection for audit, then surface
            # the error so the job dead-letters with it (no signal written).
            row.status = CANDIDATE_STATUS_REJECTED
            await session.flush()
            raise
        # The reverse link (signal -> candidate) lives on
        # ``signals_signal.source_candidate_id`` (set during promotion), so the
        # candidate row just records that it was promoted.
        row.status = CANDIDATE_STATUS_PROMOTED
        if signal.id not in seen_signal_ids:
            seen_signal_ids.add(signal.id)
            signal_ids.append(signal.id)
    await session.flush()
    return rows, signal_ids


# ---------------------------------------------------------------------------
# Stage 7b: embed (I1) — embed each stored signal into pgvector
# ---------------------------------------------------------------------------


async def embed_signals(
    session: AsyncSession,
    signal_ids: list[uuid.UUID],
    *,
    gateway: LLMGateway,
    workspace_id: str | None,
) -> int:
    """Embed the freshly-stored signals into ``signals_signal.vector_embedding`` (I1).

    Delegates to ``signals.services.embed_signals`` (the public seam — no cross-module
    model import, doc 06 §3), which builds ``title + summary`` (+ key fields) text and
    persists the pgvector embedding for fuzzy dedupe (doc 19 §7.4 / E10) + smart search
    (doc 14 §6.2 / I3). **Best-effort**: the service swallows + logs an embed failure
    and leaves the column NULL, so a failure here never loses the signal — the
    ``signals.backfill_embeddings`` sweep re-attempts. Returns the count embedded.
    """
    return await signals_services.embed_signals(
        session, signal_ids, gateway=gateway, workspace_id=workspace_id
    )


def _fields_for_payload(fields: dict[str, object]) -> dict[str, object]:
    """Return a copy of ``fields`` safe to feed the strict per-type schema gate (E4).

    Drops the ``entity_name`` convenience key the extract stage injects (doc 19 §4.3):
    it is lifted onto ``CandidateInput.entity_name`` separately, and no typed payload
    declares it while every payload ``forbid``\\s extras — so leaving it in would make
    the hard gate reject an otherwise-valid candidate whenever an entity resolved.
    """
    return {k: v for k, v in fields.items() if k != "entity_name"}


def _candidate_entity_name(candidate: CandidateRecord) -> str | None:
    """Pull the raw entity name from the candidate fields, if the extractor gave one."""
    if not isinstance(candidate.fields, dict):
        return None
    raw = candidate.fields.get("entity_name")
    return str(raw) if isinstance(raw, str) and raw.strip() else None


def _candidate_resolved_entity_id(candidate: CandidateRecord) -> uuid.UUID | None:
    """Return the resolved entity_id from the E11 two-pass extraction, if any.

    Looks at the candidate's ``entity_extraction`` result (doc 19 §4.3; E11)
    for the best resolved entity.  Falls back to ``None`` (resolution-pending)
    when no linked entity was found, so the store step behaves identically to
    the pre-E11 path for unresolved mentions.
    """
    er = candidate.entity_extraction
    if er is None or not er.entities:
        return None
    # Prefer resolved entities (resolution_pending=False) with highest confidence.
    resolved = [e for e in er.entities if not e.resolution_pending and e.entity_id is not None]
    if resolved:
        best = max(resolved, key=lambda e: e.confidence)
        return best.entity_id
    return None


def _coerce_band(raw: str | None) -> ConfidenceBand | None:
    """Map the candidate's banded-string back to the typed enum for the store step."""
    if raw is None:
        return None
    try:
        return ConfidenceBand(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_extraction_pipeline(
    session: AsyncSession,
    storage: RawDocumentStorage,
    *,
    job_id: uuid.UUID,
    raw_document_id: uuid.UUID,
    classifier: RelevanceClassifier | None = None,
    gateway: LLMGateway | None = None,
    prefilter: str,
    workspace_id: str | None = None,
    confidence_config: ConfidenceConfig = DEFAULT_CONFIG,
    ocr_backend: OcrBackend | None = None,
) -> PipelineResult:
    """Run the full funnel for one document (doc 19 §1; E1).

    fetch -> parse -> relevance gate -> extract -> score -> dedupe -> store -> embed.
    The caller (the Celery task) owns the job-status bookkeeping + retry/dead-letter;
    this function runs the stages and reports the result. It manages its **own**
    transaction boundaries: every LLM stage runs with no transaction open (so a
    pooled connection is never pinned across a network call under PgBouncer
    transaction-mode pooling — doc 06 §4), and the relevance decision + candidate
    writes commit together in one short, atomic transaction so a retry is
    all-or-nothing. On an irrelevant verdict it short-circuits before any extract
    call (the cost lever, doc 19 §3.1) and reports ``skipped=True``.

    The score stage (E6, doc 19 §6.2-§6.3) computes each candidate's banded
    confidence using ``confidence_config`` (the recipe-configurable thresholds +
    source-quality tier; defaults to the documented §6.2/§6.3 baseline). A candidate
    that lands in the ``rejected`` band is **dropped before store** — it is not
    surfaced (doc 19 §6.3) — so ``result.candidates`` is the survivors only.

    The embed step (I1) runs after store: each promoted signal is embedded into its
    ``signals_signal.vector_embedding`` pgvector column for fuzzy dedupe (doc 19 §7.4)
    + smart search (doc 14 §6.2). It is **best-effort** — an embed failure is logged
    and the column left NULL (the signal is never lost) — so it never fails the job.

    ``ocr_backend`` is injected for tests (overrides ``OCR_BACKEND`` env var) and
    forwarded to :func:`parse_document` so tests can use :class:`~.ocr.FakeOcrBackend`
    without installing Tesseract. Pass ``None`` in production (the default).
    """
    gateway = gateway or get_gateway()
    classifier = classifier or RelevanceClassifier(gateway=gateway)

    # Stage 1: fetch + parse. ``fetch_document`` only reads, so commit to release the
    # pooled DB connection before the network-bound LLM stages. Under PgBouncer
    # transaction-mode pooling an open transaction pins a server connection for the
    # whole call (doc 06 §4), and this funnel makes several LLM calls — so each LLM
    # stage runs with no transaction held, and the DB writes happen in the short
    # atomic transaction at the end.
    doc, content = await fetch_document(session, storage, raw_document_id)
    await session.commit()
    parsed = parse_document(doc, content, ocr_backend=ocr_backend)

    # Stage 2: relevance gate. Run the LLM with ``session=None`` (writes nothing,
    # holds no transaction); the decision is persisted below in the same transaction
    # as the candidates, so a Celery retry of the task can never double-record it.
    verdict = await run_relevance_gate(
        classifier,
        parsed,
        prefilter=prefilter,
        workspace_id=workspace_id,
        session=None,
    )
    if not verdict_passes(verdict):
        async with session.begin():
            await record_relevance_decision(classifier, parsed, verdict, session=session)
        return PipelineResult(
            raw_document_id=raw_document_id,
            relevant=verdict.relevant,
            skipped=True,
            candidates=[],
            signal_ids=[],
        )

    # Stages 3-4: entity extraction + signal-type detection (LLM). extract_candidates
    # releases its entity-linking reads before the signal-type LLM call internally,
    # so no transaction is held across either network call.
    raw_candidates = await extract_candidates(
        gateway, parsed, workspace_id=workspace_id, session=session
    )
    scored = [
        dedupe_candidate(score_candidate(c, config=confidence_config)) for c in raw_candidates
    ]
    # Drop ``rejected``-band candidates (< pending-review floor): they are not
    # surfaced (doc 19 §6.3). The rejected count is logged for retrospective analysis.
    survivors = [c for c in scored if not candidate_is_rejected(c)]
    rejected_count = len(scored) - len(survivors)
    if rejected_count:
        log.info(
            "extraction.score.rejected_low_confidence",
            raw_document_id=str(raw_document_id),
            recipe_id=parsed.recipe_id,
            rejected=rejected_count,
        )
    # Stages 5-6: persist the relevance decision + surviving candidates in ONE
    # transaction (no LLM here) so the write is atomic — a retry is all-or-nothing.
    async with session.begin():
        await record_relevance_decision(classifier, parsed, verdict, session=session)
        _rows, signal_ids = await store_candidates(
            session,
            job_id=job_id,
            raw_document_id=raw_document_id,
            recipe_id=parsed.recipe_id,
            candidates=survivors,
            ocr_used=parsed.ocr_used,
            ocr_truncated=parsed.ocr_truncated,
        )

    # Stage 7 (embed, I1): best-effort, and now its own short transaction (no longer
    # chained onto the whole pipeline). It must never raise — an embed failure must
    # not fail the job and trigger a retry that re-stores the candidates;
    # ``backfill_embeddings`` re-attempts NULL vectors instead (doc 19 §12.1).
    try:
        await embed_signals(session, signal_ids, gateway=gateway, workspace_id=workspace_id)
    except Exception:  # best-effort: an embed failure never fails the extraction
        log.warning(
            "extraction.embed.unexpected_failure",
            raw_document_id=str(raw_document_id),
            signal_ids=[str(s) for s in signal_ids],
            exc_info=True,
        )
        await session.rollback()
    return PipelineResult(
        raw_document_id=raw_document_id,
        relevant=True,
        skipped=False,
        candidates=survivors,
        signal_ids=signal_ids,
    )
