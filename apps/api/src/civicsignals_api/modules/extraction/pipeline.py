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

- **parse**: handles text/html/pdf; ``# TODO E9`` OCR hook for short PDFs.
- **extract**: emits permissive :class:`CandidateRecord` dicts; the **strict typed
  per-signal-type schema + required-field hard gate** (doc 19 §6.1; E4) runs in the
  store stage when a candidate is promoted into a signal.
- **score**: ``# TODO E6`` confidence blend — passthrough now.
- **dedupe**: ``# TODO E5`` per-type dedup key + merge — passthrough now.
- **store**: persists candidates to ``extraction_candidate`` (the staging row E5
  reads) **and** promotes each validated candidate into a global
  ``signals_signal`` row via ``signals.services.promote_candidate_to_signal`` (E4).
  A candidate that fails the strict schema gate raises and dead-letters the job
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
from civicsignals_api.modules.signals.services import CandidateInput, SignalValidationError

from .models import ExtractionCandidate
from .relevance import RelevanceClassifier
from .schemas import CandidateRecord, DocumentRef, ParsedDocument, RelevanceVerdict

log = structlog.get_logger(__name__)

# Grey-zone threshold (doc 19 §3.2): a "relevant" verdict below this confidence is
# the maybe band. We proceed when relevant *and* at/above this floor; the 10%
# sampling of the sub-threshold band (doc 19 §3.2) is a TODO E11 refinement —
# until then a low-confidence relevant verdict is dropped (conservative on cost).
RELEVANCE_CONFIDENCE_FLOOR: Final = 0.6

# Stage-1 OCR trigger (doc 19 §2.2): a PDF yielding < this many chars is almost
# certainly scanned/image-only and needs OCR (E9). Until E9 lands we flag the
# parse degraded and carry whatever text we got rather than dropping the doc.
OCR_MIN_PDF_CHARS: Final = 200

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
    doc 19 §3.2).
    """

    raw_document_id: uuid.UUID
    relevant: bool
    skipped: bool
    candidates: list[CandidateRecord]


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


def parse_document(doc: StoredRawDocument, content: bytes) -> ParsedDocument:
    """Extract clean text from the raw bytes (doc 19 §2.1).

    Dispatches on ``content_type``: PDF -> ``pdfplumber`` (lazy-imported; it lives
    in the ``extraction`` extra so the api image never pays for it), HTML ->
    BeautifulSoup main-text, JSON/XML/text -> decoded text. Anything unrecognised
    falls back to a best-effort UTF-8 decode and is flagged ``degraded``.

    Whitespace is collapsed to paragraph boundaries (doc 19 §2.4). Returns a
    :class:`ParsedDocument`; never raises on empty text (a relevance gate /
    downstream stage decides what an empty parse means).
    """
    content_type = (doc.content_type or "").lower()
    degraded = False

    if "pdf" in content_type:
        text = _parse_pdf(content)
        # TODO E9: OCR decision (doc 19 §2.2). A PDF yielding < OCR_MIN_PDF_CHARS is
        # almost certainly scanned/image-only and needs OCR (Tesseract/Textract).
        # Until E9 lands we flag the parse degraded rather than dropping the doc, so
        # the snapshot is still replayable once OCR ships.
        if len(text) < OCR_MIN_PDF_CHARS:
            degraded = True
            log.info(
                "extraction.parse.pdf_below_ocr_threshold",
                raw_document_id=str(doc.id),
                chars=len(text),
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
    )


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


def _parse_pdf(content: bytes) -> str:
    """Extract the text layer from a PDF via ``pdfplumber`` (doc 19 §2.1).

    ``pdfplumber`` lives in the ``extraction`` extra (installed on
    ``worker_extract``, not the api image), so it is imported lazily. If it is
    unavailable we degrade to empty text rather than crashing the import of any
    process that merely imports this module.
    """
    try:
        import pdfplumber
    except ImportError:  # pragma: no cover - depends on the extraction extra
        log.warning("extraction.parse.pdfplumber_unavailable")
        return ""

    import io

    # A malformed/encrypted/truncated PDF can make pdfplumber (pdfminer under it)
    # raise on open or per-page extraction. Parse is best-effort (doc 19 §2.1): a
    # bad PDF must degrade to empty text — which the caller flags degraded and the
    # # TODO E9 OCR hook later re-attempts — not crash the whole job.
    try:
        parts: list[str] = []
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                if page_text:
                    parts.append(page_text)
        return "\n\n".join(parts)
    except Exception as exc:  # pdfminer raises a broad family of parse errors
        log.warning("extraction.parse.pdf_failed", error=str(exc))
        return ""


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
    (see :func:`verdict_passes`).
    """
    ref = DocumentRef(
        raw_document_id=str(parsed.raw_document_id),
        recipe_id=parsed.recipe_id,
        source=parsed.source_url,
        text=parsed.text,
    )
    return await classifier.classify(
        ref, prefilter=prefilter, workspace_id=workspace_id, session=session
    )


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
) -> list[CandidateRecord]:
    """Emit candidate signal records from the document via the gateway (doc 19 §5).

    Routes through the LLM gateway (``TASK_EXTRACTION`` -> a Sonnet-class model)
    with the versioned ``signal_extraction`` prompt. Returns permissive
    :class:`CandidateRecord` objects.

    # TODO E4: the deterministic Stage-3 first pass (spaCy NER + regex, doc 19
    # §4.1) and the strict typed per-signal-type schemas + required-field hard gate
    # (doc 19 §5.1, §6.1) replace this single permissive LLM pass. The free-form
    # ``fields`` dict is the seam E4 reads from.
    """
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
            )
        ]
    return candidates


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
# Stage 5: score (confidence) — passthrough stub
# ---------------------------------------------------------------------------


def score_candidate(candidate: CandidateRecord) -> CandidateRecord:
    """Assign an extraction-confidence score (doc 19 §6.2).

    # TODO E6: the real weighted blend (field-level confidences, LLM self-report,
    # source quality, schema completeness, cross-validation — doc 19 §6.2) plus the
    # confidence-band thresholds (doc 19 §6.3). Passthrough now: keep the model's
    # self-reported confidence (already capped at 0.95), defaulting to a neutral
    # mid value when the model didn't report one.
    """
    if candidate.confidence is not None:
        return candidate
    return candidate.model_copy(update={"confidence": 0.5})


# ---------------------------------------------------------------------------
# Stage 6: dedupe — passthrough stub
# ---------------------------------------------------------------------------


def dedupe_candidate(candidate: CandidateRecord) -> CandidateRecord:
    """Compute the dedup key and (eventually) merge duplicates (doc 19 §7).

    # TODO E5: the per-signal-type dedup key (doc 19 §7.1), the windowed lookup
    # against ``signals_signal`` (doc 19 §7.2), the merge logic (doc 19 §7.3), and
    # the embedding-based fuzzy fallback (doc 19 §7.4). Passthrough now: compute a
    # coarse placeholder key so the column is populated and E5 has a starting
    # point, but perform no merge (every candidate is stored as new).
    """
    if candidate.dedup_key is not None:
        return candidate
    title = ""
    if isinstance(candidate.fields, dict):
        raw_title = candidate.fields.get("title") or candidate.fields.get("summary")
        title = str(raw_title).strip() if raw_title is not None else ""
    # Without any title/summary text the key would be a bare ``type:`` with no
    # distinguishing content — useless for dedupe — so leave it None for E5 to fill.
    if not title:
        return candidate
    placeholder = f"{candidate.signal_type or 'unknown'}:{title}".lower()[:255]
    return candidate.model_copy(update={"dedup_key": placeholder})


# ---------------------------------------------------------------------------
# Stage 7: store (persist candidates)
# ---------------------------------------------------------------------------


# Status the candidate row carries once it is promoted / rejected (mirrors
# ``extraction_candidate.status``; doc 19 §5.1, §6.1).
CANDIDATE_STATUS_PROMOTED: Final = "promoted"
CANDIDATE_STATUS_REJECTED: Final = "rejected"


def _candidate_content_hash(candidate: CandidateRecord) -> str:
    """Derive a coarse dedupe ``content_hash`` for the signal row (doc 19 §7.1).

    E5 lands the real per-signal-type canonical dedupe key + windowed merge. Until
    then we hash the dedupe-stage placeholder key (or, if it computed none, the
    signal type + a stable digest of the fields) so the ``signals_signal`` unique
    dedupe index is populated and re-promotion of the *same* candidate is idempotent
    rather than tripping the constraint.
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

    # TODO E5: dedupe before store (per-type key + windowed merge, doc 19 §7) —
    # E4 promotes with the exact-key upsert only.
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
        )
        session.add(row)
        rows.append(row)
    await session.flush()  # assign candidate ids before promotion links to them

    signal_ids: list[uuid.UUID] = []
    seen_signal_ids: set[uuid.UUID] = set()
    for candidate, row in zip(candidates, rows, strict=True):
        candidate_input = CandidateInput(
            signal_type=candidate.signal_type,
            fields=dict(candidate.fields),
            recipe_id=recipe_id,
            raw_document_id=raw_document_id,
            content_hash=_candidate_content_hash(candidate),
            # TODO E10: entity resolution (doc 19 §4.3) supplies a resolved
            # entity_id; until then the signal is stored resolution-pending (the
            # service flags it review_required) with the raw name from the fields.
            entity_id=None,
            entity_name=_candidate_entity_name(candidate),
            confidence=candidate.confidence,
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


def _candidate_entity_name(candidate: CandidateRecord) -> str | None:
    """Pull the raw entity name from the candidate fields, if the extractor gave one."""
    if not isinstance(candidate.fields, dict):
        return None
    raw = candidate.fields.get("entity_name")
    return str(raw) if isinstance(raw, str) and raw.strip() else None


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
) -> PipelineResult:
    """Run the full funnel for one document (doc 19 §1; E1).

    fetch -> parse -> relevance gate -> extract -> score -> dedupe -> store -> embed.
    The caller (the Celery task) owns the transaction *and* the job-status bookkeeping
    + retry/dead-letter; this function just runs the stages and reports the result.
    On an irrelevant verdict it short-circuits before any extract call (the cost
    lever, doc 19 §3.1) and reports ``skipped=True``.

    The embed step (I1) runs after store: each promoted signal is embedded into its
    ``signals_signal.vector_embedding`` pgvector column for fuzzy dedupe (doc 19 §7.4)
    + smart search (doc 14 §6.2). It is **best-effort** — an embed failure is logged
    and the column left NULL (the signal is never lost) — so it never fails the job.
    """
    gateway = gateway or get_gateway()
    classifier = classifier or RelevanceClassifier(gateway=gateway)

    doc, content = await fetch_document(session, storage, raw_document_id)
    parsed = parse_document(doc, content)

    verdict = await run_relevance_gate(
        classifier,
        parsed,
        prefilter=prefilter,
        workspace_id=workspace_id,
        session=session,
    )
    if not verdict_passes(verdict):
        return PipelineResult(
            raw_document_id=raw_document_id,
            relevant=verdict.relevant,
            skipped=True,
            candidates=[],
        )

    raw_candidates = await extract_candidates(gateway, parsed, workspace_id=workspace_id)
    scored = [dedupe_candidate(score_candidate(c)) for c in raw_candidates]
    _rows, signal_ids = await store_candidates(
        session,
        job_id=job_id,
        raw_document_id=raw_document_id,
        recipe_id=parsed.recipe_id,
        candidates=scored,
    )
    await embed_signals(session, signal_ids, gateway=gateway, workspace_id=workspace_id)
    return PipelineResult(
        raw_document_id=raw_document_id,
        relevant=True,
        skipped=False,
        candidates=scored,
    )
