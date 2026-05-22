"""Two-pass entity extraction for the Stage-3 pipeline step (doc 19 §4; E11).

Two-pass strategy (doc 19 §4.1):

**Pass 1 — LLM candidate extraction (Haiku-class, cheap):**
  Runs the ``entity_extraction/v2`` prompt against the document text to surface
  candidate named entities: organizations, persons, monetary amounts, dates,
  product categories, vendor names, and contract terms.  Output is permissive
  (false-positives preferred over misses).

**Pass 2 — LLM refinement + entity linking (Sonnet-class):**
  Triggered when Pass 1 found fewer than ``MIN_ENTITY_TYPES_FOR_SINGLE_PASS``
  distinct entity types, or when Pass 1's overall confidence is below
  ``PASS2_CONFIDENCE_THRESHOLD``.  Sends the document text plus the Pass-1
  partial extraction to ``entity_extraction/v1`` (the Sonnet-class validation
  prompt) for normalisation, disambiguation, and gap-filling.

**Entity linking:**
  After the combined Pass-1 / Pass-2 extraction, each organisation mention is
  looked up via ``entities.services.search_entities`` (the public cross-module
  seam — doc 06 §3).  A name-match links the mention to a canonical
  ``entities_entity`` row (``entity_id`` + ``entity_name`` filled in); an
  unresolved mention is flagged ``resolution_pending=True``.

**Graceful degradation:**
  If Pass 2 fails for any reason, we fall back to Pass-1 results with a
  ``degraded=True`` flag so the signal is never lost (doc 19 §4.1 /
  §12.1).  If Pass 1 also fails, an empty ``EntityExtractionResult`` (with
  ``degraded=True``) is returned — downstream stages proceed with no entity
  data rather than crashing.

**Efficiency:**
  For *trivially* small documents (fewer than ``MIN_CHARS_FOR_PASS2`` characters
  after truncation) we skip Pass 2 even when confidence is low — there is simply
  nothing more to extract from a very short text, and the Sonnet call would not
  help.
"""

from __future__ import annotations

import json
import uuid
from typing import Final

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.llm_gateway import TASK_EXTRACTION, LLMGateway
from civicsignals_api.modules.entities import services as entities_services
from civicsignals_api.modules.entities.services import EntityFilters

from .schemas import EntityExtractionResult, ExtractedEntity

log = structlog.get_logger(__name__)

# Prompt names / versions for the two passes (doc 19 §5.3 / E3).
# Pass 1: cheap Haiku-class candidate extraction.
PASS1_PROMPT_NAME: Final = "entity_extraction"
PASS1_PROMPT_VERSION: Final = "v2"
# Pass 2: Sonnet-class validation + refinement (pre-existing v1 prompt).
PASS2_PROMPT_NAME: Final = "entity_extraction"
PASS2_PROMPT_VERSION: Final = "v1"

# If Pass 1 surfaced fewer distinct entity types than this, trigger Pass 2.
MIN_ENTITY_TYPES_FOR_SINGLE_PASS: Final = 3

# If Pass 1's overall confidence is below this, trigger Pass 2 even when the
# type-count threshold is met.
PASS2_CONFIDENCE_THRESHOLD: Final = 0.6

# Very short documents cannot benefit from Pass 2 (no extra information to
# find).  Skip it to avoid a pointless expensive call.
MIN_CHARS_FOR_PASS2: Final = 200

# Truncation budget fed to both LLM passes.  Bounded so token costs stay
# predictable (doc 19 §5.4); matches the signal_extraction budget.
MAX_ENTITY_EXTRACT_CHARS: Final = 24_000

# Maximum number of entity-linking DB lookups per extraction call (guard
# against pathological documents with thousands of organisation mentions).
MAX_LINK_LOOKUPS: Final = 20


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip(), True


# ---------------------------------------------------------------------------
# Pass-1 output parsing
# ---------------------------------------------------------------------------


def _parse_entity_json(text: str) -> dict[str, object] | None:
    """Extract the first JSON object from a possibly-prose LLM reply.

    Returns ``None`` on any parse failure — callers degrade gracefully.
    """
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _count_entity_types(data: dict[str, object]) -> int:
    """Count how many of the expected entity-type lists are non-empty."""
    type_keys = (
        "organizations",
        "persons",
        "monetary_amounts",
        "dates",
        "products_categories",
        "vendors_mentioned",
        "contract_terms_mentions",
    )
    count = 0
    for k in type_keys:
        val = data.get(k)
        if isinstance(val, list) and len(val) > 0:
            count += 1
    return count


def _extract_confidence(data: dict[str, object]) -> float:
    """Pull the overall extraction_confidence from a parsed response dict."""
    raw = data.get("extraction_confidence")
    if raw is None:
        return 0.0
    try:
        return max(0.0, min(0.95, float(raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _coerce_entity_list(data: dict[str, object], field: str) -> list[dict[str, object]]:
    """Return the named list from the parsed dict, filtering to dicts only."""
    raw = data.get(field)
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _coerce_str_list(data: dict[str, object], field: str) -> list[str]:
    raw = data.get(field)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if item is not None]


def _merge_entity_data(
    pass1: dict[str, object],
    pass2: dict[str, object] | None,
) -> dict[str, object]:
    """Merge Pass-2 results on top of Pass-1, preferring Pass-2 where non-empty.

    For list fields: if Pass 2 returned a non-empty list, use it (it has been
    validated / gap-filled); otherwise keep Pass 1's.  For scalar fields
    (extraction_confidence, extraction_warnings) use Pass 2 when present.
    This keeps Pass-1 data when Pass 2 simply echoed it or did nothing.
    """
    if pass2 is None:
        return pass1

    list_fields = (
        "organizations",
        "persons",
        "monetary_amounts",
        "dates",
        "products_categories",
        "vendors_mentioned",
        "contract_terms_mentions",
        "raw_keywords",
        "extraction_warnings",
    )
    merged: dict[str, object] = dict(pass1)
    for field in list_fields:
        p2_val = pass2.get(field)
        if isinstance(p2_val, list) and len(p2_val) > 0:
            merged[field] = p2_val

    # Use the higher confidence score as the merged overall confidence.
    p1_conf = _extract_confidence(pass1)
    p2_conf = _extract_confidence(pass2)
    merged["extraction_confidence"] = max(p1_conf, p2_conf)

    return merged


def _build_extraction_result(
    merged: dict[str, object],
    *,
    method: str,
    degraded: bool = False,
) -> EntityExtractionResult:
    """Build a typed :class:`EntityExtractionResult` from the merged dict."""
    orgs = _coerce_entity_list(merged, "organizations")
    persons = _coerce_entity_list(merged, "persons")
    amounts = _coerce_entity_list(merged, "monetary_amounts")
    dates = _coerce_entity_list(merged, "dates")
    products = _coerce_str_list(merged, "products_categories")
    vendors = _coerce_entity_list(merged, "vendors_mentioned")
    contracts = _coerce_entity_list(merged, "contract_terms_mentions")
    keywords = _coerce_str_list(merged, "raw_keywords")
    warnings = _coerce_str_list(merged, "extraction_warnings")
    confidence = _extract_confidence(merged)

    # Build ExtractedEntity list from organizations (the primary linking target).
    entities: list[ExtractedEntity] = []
    for org in orgs:
        name = org.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        conf_raw = org.get("confidence")
        try:
            conf = max(0.0, min(0.95, float(conf_raw)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            conf = confidence  # fall back to overall confidence
        entities.append(
            ExtractedEntity(
                raw_name=name.strip(),
                confidence=conf,
                resolution_pending=True,  # will be resolved by entity linking
            )
        )

    # Also surface vendor mentions as entities (unresolved; linking pass
    # may connect them to entity rows if names match canonical names).
    for vendor in vendors:
        name = vendor.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        conf_raw = vendor.get("confidence")
        try:
            conf = max(0.0, min(0.95, float(conf_raw)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            conf = confidence
        entities.append(
            ExtractedEntity(
                raw_name=name.strip(),
                confidence=conf,
                resolution_pending=True,
            )
        )

    return EntityExtractionResult(
        entities=entities,
        organizations=orgs,
        persons=persons,
        monetary_amounts=amounts,
        dates=dates,
        products_categories=products,
        vendors_mentioned=vendors,
        contract_terms_mentions=contracts,
        raw_keywords=keywords,
        extraction_confidence=confidence,
        extraction_warnings=warnings,
        extraction_method=method,
        degraded=degraded,
    )


# ---------------------------------------------------------------------------
# Entity linking (entities service cross-module seam)
# ---------------------------------------------------------------------------


async def _link_entities(
    result: EntityExtractionResult,
    session: AsyncSession,
) -> EntityExtractionResult:
    """Resolve each extracted entity mention to a canonical entity row (doc 19 §4.3).

    Uses ``entities.services.search_entities`` (the public seam — doc 06 §3) to
    look up each organisation/vendor mention by name.  On an exact-ish match
    (the service uses ILIKE; we take the first result as the best candidate)
    the mention gains ``entity_id`` and ``entity_name``, and
    ``resolution_pending`` is cleared.

    Unresolved mentions keep ``resolution_pending=True`` — the pipeline stores
    the signal with the raw name and the human-review flag; once the entity is
    resolved (or created) all pending signals re-link automatically (doc 19 §4.3).

    Caps at ``MAX_LINK_LOOKUPS`` to bound DB call counts on long documents.
    """
    if not result.entities:
        return result

    linked: list[ExtractedEntity] = []
    seen_names: set[str] = set()
    lookup_count = 0

    for entity in result.entities:
        name_key = entity.raw_name.lower()
        if name_key in seen_names:
            # Duplicate mention: carry the first resolved/unresolved state.
            existing = next(e for e in linked if e.raw_name.lower() == name_key)
            linked.append(existing.model_copy())
            continue
        seen_names.add(name_key)

        if lookup_count >= MAX_LINK_LOOKUPS:
            linked.append(entity)
            continue

        try:
            page = await entities_services.search_entities(
                session,
                EntityFilters(q=entity.raw_name),
                limit=1,
            )
            lookup_count += 1

            if page.items:
                canonical = page.items[0]
                linked.append(
                    entity.model_copy(
                        update={
                            "entity_id": canonical.id,
                            "entity_name": canonical.name,
                            "resolution_pending": False,
                        }
                    )
                )
                log.info(
                    "extraction.entity_extraction.linked",
                    raw_name=entity.raw_name,
                    entity_id=str(canonical.id),
                    entity_name=canonical.name,
                )
            else:
                linked.append(entity)
                log.debug(
                    "extraction.entity_extraction.unresolved",
                    raw_name=entity.raw_name,
                )
        except Exception as exc:
            # Entity linking is best-effort — a DB error must not lose the signal.
            log.warning(
                "extraction.entity_extraction.link_failed",
                raw_name=entity.raw_name,
                error=str(exc),
                exc_info=True,
            )
            linked.append(entity)

    return result.model_copy(update={"entities": linked})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_entity_extraction(
    gateway: LLMGateway,
    document_text: str,
    *,
    workspace_id: str | None,
    session: AsyncSession | None = None,
    raw_document_id: uuid.UUID | None = None,
) -> EntityExtractionResult:
    """Run the two-pass entity extraction and return a typed result (doc 19 §4; E11).

    **Pass 1** — ``entity_extraction/v2`` (Haiku-class): extract candidate
    entities cheaply.  Always runs.

    **Pass 2** — ``entity_extraction/v1`` (Sonnet-class): refine, validate, and
    fill gaps.  Triggered when:
    - Pass 1 found fewer than :data:`MIN_ENTITY_TYPES_FOR_SINGLE_PASS` distinct
      entity types, **or**
    - Pass 1's overall confidence is below :data:`PASS2_CONFIDENCE_THRESHOLD`.
    Skipped for trivially-short documents (< :data:`MIN_CHARS_FOR_PASS2` chars)
    even when those conditions hold — no additional information can be recovered.

    **Entity linking** — resolves extracted organisation/vendor names to
    canonical ``entities_entity`` rows via ``entities.services.search_entities``
    when a ``session`` is supplied.  Skipped (entities stay ``resolution_pending``)
    when ``session`` is ``None`` (e.g. in lightweight unit tests).

    On any failure the function degrades gracefully rather than raising:
    - Pass-2 failure → fall back to Pass-1, set ``degraded=True``.
    - Pass-1 failure → return an empty result with ``degraded=True``.

    Token accounting is recorded against ``workspace_id`` for each gateway call
    (the gateway already handles this — doc 06 §7).
    """
    doc_id_str = str(raw_document_id) if raw_document_id else "unknown"
    truncated_text, _was_truncated = _truncate(document_text, MAX_ENTITY_EXTRACT_CHARS)

    # ------------------------------------------------------------------
    # Pass 1: cheap candidate extraction
    # ------------------------------------------------------------------
    pass1_data: dict[str, object] | None = None
    try:
        result1 = await gateway.complete(
            task=TASK_EXTRACTION,
            prompt_name=PASS1_PROMPT_NAME,
            prompt_version=PASS1_PROMPT_VERSION,
            prompt_vars={"document_text": truncated_text},
            max_tokens=1024,
            workspace_id=workspace_id,
        )
        pass1_data = _parse_entity_json(result1.text)
        if pass1_data is None:
            log.warning(
                "extraction.entity_extraction.pass1_unparseable",
                raw_document_id=doc_id_str,
                model=result1.model,
            )
    except Exception as exc:
        log.warning(
            "extraction.entity_extraction.pass1_failed",
            raw_document_id=doc_id_str,
            error=str(exc),
            exc_info=True,
        )
        # Pass-1 total failure → empty degraded result; downstream proceeds
        # without entity data (doc 19 §12.1 best-effort).
        return EntityExtractionResult(degraded=True, extraction_method="failed")

    if pass1_data is None:
        return EntityExtractionResult(degraded=True, extraction_method="failed")

    pass1_type_count = _count_entity_types(pass1_data)
    pass1_confidence = _extract_confidence(pass1_data)

    log.debug(
        "extraction.entity_extraction.pass1_done",
        raw_document_id=doc_id_str,
        entity_types_found=pass1_type_count,
        confidence=pass1_confidence,
    )

    # ------------------------------------------------------------------
    # Pass 2 decision: is a refinement call worth it?
    # ------------------------------------------------------------------
    needs_pass2 = (
        pass1_type_count < MIN_ENTITY_TYPES_FOR_SINGLE_PASS
        or pass1_confidence < PASS2_CONFIDENCE_THRESHOLD
    )
    too_short_for_pass2 = len(truncated_text) < MIN_CHARS_FOR_PASS2

    pass2_data: dict[str, object] | None = None
    extraction_method = "deterministic"  # single pass ≈ deterministic fast path

    if needs_pass2 and not too_short_for_pass2:
        extraction_method = "llm_assisted"
        # Serialize Pass-1 partial result for the Pass-2 prompt variable.
        partial_json = json.dumps(pass1_data, default=str, indent=2)
        try:
            result2 = await gateway.complete(
                task=TASK_EXTRACTION,
                prompt_name=PASS2_PROMPT_NAME,
                prompt_version=PASS2_PROMPT_VERSION,
                prompt_vars={
                    "document_text": truncated_text,
                    "partial_entities": partial_json,
                },
                max_tokens=2048,
                workspace_id=workspace_id,
            )
            pass2_data = _parse_entity_json(result2.text)
            if pass2_data is None:
                log.warning(
                    "extraction.entity_extraction.pass2_unparseable",
                    raw_document_id=doc_id_str,
                    model=result2.model,
                )
        except Exception as exc:
            # Pass-2 failure is degraded but not fatal (doc 19 §4.1 / §12.1):
            # keep Pass-1 results with the degraded flag set.
            log.warning(
                "extraction.entity_extraction.pass2_failed",
                raw_document_id=doc_id_str,
                error=str(exc),
                exc_info=True,
            )
            # pass2_data remains None → _merge_entity_data will keep pass1 only
    else:
        if needs_pass2 and too_short_for_pass2:
            log.debug(
                "extraction.entity_extraction.pass2_skipped_short_doc",
                raw_document_id=doc_id_str,
                chars=len(truncated_text),
            )
        else:
            log.debug(
                "extraction.entity_extraction.pass2_skipped_sufficient",
                raw_document_id=doc_id_str,
                entity_types_found=pass1_type_count,
                confidence=pass1_confidence,
            )

    # Pass-2 failed/skipped and was needed → the result is degraded.
    degraded = needs_pass2 and not too_short_for_pass2 and pass2_data is None

    merged = _merge_entity_data(pass1_data, pass2_data)
    entity_result = _build_extraction_result(merged, method=extraction_method, degraded=degraded)

    # ------------------------------------------------------------------
    # Entity linking (best-effort; skipped when session is None)
    # ------------------------------------------------------------------
    if session is not None and entity_result.entities:
        entity_result = await _link_entities(entity_result, session)

    return entity_result
