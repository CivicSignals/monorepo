"""Public service interface for the signals module.

Other modules call signals only through the functions defined here — never by
importing signals's models or routes directly (doc 06 §3).

E4 lands:

- the strict per-signal-type schema gate (re-exported from ``schemas``);
- :func:`promote_candidate_to_signal` — the extraction funnel's "store" step
  (doc 19 §1, §6.1): validate a candidate against its strict schema, then upsert a
  global ``signals_signal`` row. E1's pipeline calls this from its ``store`` stage.
- :func:`get_signal` / :func:`list_signals` — the read seam the G1 feed and F3
  scoring (doc 14) build on. Signals are **global** (doc 07 §3): the list filters
  by entity / signal type / date, not by workspace — per-workspace scoring is a
  separate table (``signals_workspace_score``, F3).

Cursor pagination uses the same keyset strategy as the other modules (doc 06 §5):
the UUID v7 id is time-ordered, so ``observed_at DESC, id DESC`` is a stable feed
order and a base64-encoded id is the opaque cursor.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .dedupe import (
    DEDUPE_WINDOWS,
    DEFAULT_DEDUPE_WINDOW,
    compute_dedupe_hash,
    compute_dedupe_hash_for_payload,
    find_duplicate,
    merge_signal,
    window_for,
)
from .embedding import (
    EmbeddingDimMismatchError,
    backfill_embeddings,
    build_embedding_text,
    embed_signals,
    embedding_text_for_signal,
)
from .models import (
    SIGNAL_STATUS_NEW,
    SIGNAL_STATUS_PENDING_REVIEW,
    Signal,
)
from .schemas import (
    PAYLOAD_BY_TYPE,
    SignalPage,
    SignalPayload,
    SignalRead,
    SignalType,
    SignalValidationError,
    parse_signal_payload,
)
from .scoring import (
    DEFAULT_CONFIG,
    BandThresholds,
    ConfidenceBand,
    ConfidenceConfig,
    ConfidenceWeights,
    ScoreResult,
    config_from_recipe,
    score_candidate_confidence,
)

# Pagination defaults (doc 06 §5, doc 08 §1.5).
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------


def encode_cursor(row_id: uuid.UUID) -> str:
    """Encode a UUID keyset cursor as an opaque URL-safe base64 token."""
    return base64.urlsafe_b64encode(row_id.bytes).decode("ascii")


def decode_cursor(cursor: str) -> uuid.UUID:
    """Decode a cursor token back to a UUID, or raise ``ValueError``."""
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(cursor.encode("ascii")))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid cursor") from exc


# ---------------------------------------------------------------------------
# Promote candidate -> signal (the extraction funnel's store step)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CandidateInput:
    """The validated-or-not candidate the extraction funnel hands to the store step.

    Carries the coarse ``signal_type`` + permissive ``fields`` (the extract stage's
    output), the per-document provenance (recipe, raw documents, extraction job +
    candidate ids), the score-stage ``confidence`` and the dedupe-stage
    ``content_hash``. :func:`promote_candidate_to_signal` runs the strict schema gate
    over ``signal_type`` + ``fields`` and lifts the validated payload onto a row.

    ``entity_id`` may be ``None`` (resolution pending, doc 19 §4.3); when so, the raw
    ``entity_name`` (from the candidate fields, if any) is kept and the signal is
    flagged ``review_required``.
    """

    signal_type: str | None
    fields: dict[str, object]
    recipe_id: str
    raw_document_id: uuid.UUID
    content_hash: str
    entity_id: uuid.UUID | None = None
    entity_name: str | None = None
    confidence: float | None = None
    # The confidence band the E6 scorer assigned (doc 19 §6.3). When the score stage
    # computed it, the band drives the row's status/degraded/review flags directly.
    # When ``None`` (a non-pipeline caller that only set ``confidence``), the band is
    # derived from the confidence against the default thresholds — so the documented
    # §6.3 banding still applies. A ``rejected``-band candidate must not reach here:
    # the pipeline drops it before the store step (doc 19 §6.3).
    band: ConfidenceBand | None = None
    occurred_at: datetime | None = None
    extraction_job_id: uuid.UUID | None = None
    source_candidate_id: uuid.UUID | None = None
    extra_raw_document_ids: list[uuid.UUID] = field(default_factory=list)


async def promote_candidate_to_signal(
    session: AsyncSession,
    candidate: CandidateInput,
) -> Signal:
    """Validate + promote a candidate into a global ``signals_signal`` row (E4).

    The funnel's "store" step (doc 19 §1, §6.1). Two parts:

    1. **Strict gate** (doc 19 §6.1, hard gate): the candidate's ``signal_type`` +
       ``fields`` are validated against the matching per-type schema via
       :func:`parse_signal_payload`. An invalid candidate raises
       :class:`SignalValidationError` — the extraction task turns that into the
       retry → dead-letter path with the surfaced error. **No partial signal is
       written on a validation failure.**
    2. **Windowed dedupe** (doc 19 §7): compute the canonical per-type dedupe hash
       from ``entity_id + signal_type + normalized_key_fields`` (``signals.dedupe``),
       look for an existing signal with that hash within the type-specific window
       (90d RFP, 365d contracts/budgets, 730d leadership, 60d board agenda — doc 19
       §7.1); on a hit **merge** the new corroborating document(s) + higher
       confidence into the surviving signal (doc 19 §7.3, no source doc lost); else
       INSERT a new signal.

    The caller owns the transaction (this flushes, not commits).

    The confidence band (doc 19 §6.2-§6.3; E6) is supplied on ``candidate.band``
    when the extraction score stage computed it, or derived from
    ``candidate.confidence`` against the default thresholds otherwise; it sets the
    row's ``status``/``is_degraded``/``review_required`` flags. A ``rejected``-band
    candidate must not reach here — the pipeline drops it before store (doc 19 §6.3).

    # TODO E10/I1: the embedding-based **fuzzy** dedupe fallback (doc 19 §7.4) for
    # high-stakes types — this is the exact-key half (doc 19 §7.1-§7.3) only.
    """
    payload = parse_signal_payload(candidate.signal_type, candidate.fields)
    return await store_signal(session, payload, candidate)


async def store_signal(
    session: AsyncSession,
    payload: SignalPayload,
    candidate: CandidateInput,
) -> Signal:
    """Store a validated payload into ``signals_signal`` with windowed dedupe (doc 19 §7).

    Separated from :func:`promote_candidate_to_signal` so a caller that already
    holds a validated :class:`SignalPayload` (e.g. a backfill or a test) can store
    it directly. Computes the canonical per-type dedupe hash, runs the type-windowed
    duplicate lookup (doc 19 §7.2), and either merges into the surviving signal
    (doc 19 §7.3) or inserts a new one. The caller commits.
    """
    band = _resolve_band(candidate)
    review = band is ConfidenceBand.PENDING_REVIEW or candidate.entity_id is None
    degraded = band is ConfidenceBand.DEGRADED
    details = payload.model_dump(mode="json")
    raw_doc_ids = _ordered_unique([candidate.raw_document_id, *candidate.extra_raw_document_ids])
    now = datetime.now(UTC)

    # Canonical per-type dedupe hash (doc 19 §7.1): entity_id + signal_type +
    # normalized key fields. This supersedes the coarse placeholder ``content_hash``
    # the extract stage carried — the store path is the authority on the dedupe key.
    dedupe_hash = compute_dedupe_hash_for_payload(payload, candidate.entity_id)

    # Windowed lookup (doc 19 §7.2): is there an existing signal with this key inside
    # the type-specific window? If so, merge the new evidence into it (doc 19 §7.3)
    # rather than inserting a duplicate.
    existing = await find_duplicate(
        session,
        entity_id=candidate.entity_id,
        signal_type=payload.signal_type,
        dedupe_hash=dedupe_hash,
        now=now,
    )
    if existing is not None:
        merge_signal(
            existing,
            new_doc_ids=raw_doc_ids,
            new_confidence=candidate.confidence,
            now=now,
        )
        await session.flush()
        return existing

    # No duplicate within the window — a new signal (doc 19 §7.2 step 3).
    row = Signal(
        id=uuid.uuid4(),
        entity_id=candidate.entity_id,
        entity_name_raw=candidate.entity_name,
        signal_type=payload.signal_type.value,
        recipe_id=candidate.recipe_id,
        extraction_job_id=candidate.extraction_job_id,
        source_candidate_id=candidate.source_candidate_id,
        raw_document_ids=[str(d) for d in raw_doc_ids],
        content_hash=dedupe_hash,
        occurred_at=candidate.occurred_at,
        observed_at=now,
        title=payload.title,
        summary=payload.summary,
        details=details,
        confidence=candidate.confidence,
        status=SIGNAL_STATUS_PENDING_REVIEW if review else SIGNAL_STATUS_NEW,
        is_degraded=degraded,
        review_required=review,
    )
    session.add(row)
    await session.flush()
    return row


def _resolve_band(candidate: CandidateInput) -> ConfidenceBand:
    """Resolve the confidence band for a candidate at store time (doc 19 §6.3).

    Prefers the band the E6 score stage already computed (``candidate.band``). When
    absent (a non-pipeline caller that set only ``confidence``), derive it from the
    confidence against the default thresholds so the documented §6.3 banding still
    applies. A ``None`` confidence with no band is treated as ``normal`` — a missing
    score does not by itself hold a signal for review; the separate
    entity-resolution trigger (doc 19 §4.3) still forces review in the caller.
    """
    if candidate.band is not None:
        return candidate.band
    if candidate.confidence is None:
        return ConfidenceBand.NORMAL
    return DEFAULT_CONFIG.thresholds.band_for(candidate.confidence)


def _ordered_unique(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """De-duplicate a list of ids preserving first-seen order."""
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def dedupe_key_for_candidate(
    signal_type: str | None,
    fields: dict[str, object],
    entity_id: uuid.UUID | None = None,
) -> str | None:
    """Compute the canonical dedupe hash for a pre-validation candidate (doc 19 §7.1).

    The seam the extraction funnel's ``dedupe_candidate`` stage calls (doc 19 §7):
    it has only the coarse ``signal_type`` + permissive ``fields`` (not yet the
    strict typed payload), so this resolves the type and hashes the per-type key
    fields directly. Returns ``None`` for an unknown/absent signal type — the stage
    leaves the placeholder key and the store path computes the authoritative hash
    from the validated payload. Cross-module callers reach this through
    ``signals.services`` (never ``signals.dedupe`` directly — doc 06 §3).
    """
    if signal_type is None:
        return None
    try:
        type_enum = SignalType(signal_type)
    except ValueError:
        return None
    return compute_dedupe_hash(type_enum, entity_id, fields)


# ---------------------------------------------------------------------------
# Read seam (G1 feed / F3 scoring)
# ---------------------------------------------------------------------------


async def get_signal(session: AsyncSession, signal_id: uuid.UUID) -> SignalRead | None:
    """Fetch one global signal by id (doc 08; the signal-detail read seam)."""
    row = await session.get(Signal, signal_id)
    return SignalRead.model_validate(row) if row is not None else None


async def list_signals(
    session: AsyncSession,
    *,
    entity_id: uuid.UUID | None = None,
    signal_type: str | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> SignalPage:
    """Cursor-paginated list of global signals, newest first (doc 06 §5, doc 14).

    Signals are global (doc 07 §3): the filters are by entity / signal type /
    occurred-date — **not** by workspace. Per-workspace relevance is the F3
    ``signals_workspace_score`` table; G1's feed joins that, but this raw list seam
    powers the global signal browse + F3's backfill pre-filter (doc 14 §6.1, §7).

    Ordered ``observed_at DESC, id DESC``. The UUID v7 id is time-ordered, so it is a
    stable tiebreaker and the opaque cursor; keyset paging compares against the
    cursor row's ``(observed_at, id)``.
    """
    limit = max(1, min(limit, MAX_LIMIT))

    stmt = select(Signal)
    if entity_id is not None:
        stmt = stmt.where(Signal.entity_id == entity_id)
    if signal_type is not None:
        stmt = stmt.where(Signal.signal_type == signal_type)
    if occurred_after is not None:
        stmt = stmt.where(Signal.occurred_at >= occurred_after)
    if occurred_before is not None:
        stmt = stmt.where(Signal.occurred_at <= occurred_before)

    if cursor is not None:
        cursor_id = decode_cursor(cursor)
        cursor_row = await session.get(Signal, cursor_id)
        if cursor_row is None:
            raise ValueError("invalid cursor")
        # Keyset on (observed_at DESC, id DESC): strictly older than the cursor row.
        stmt = stmt.where(
            (Signal.observed_at < cursor_row.observed_at)
            | ((Signal.observed_at == cursor_row.observed_at) & (Signal.id < cursor_id))
        )

    stmt = stmt.order_by(Signal.observed_at.desc(), Signal.id.desc()).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return SignalPage(
        items=[SignalRead.model_validate(r) for r in items],
        next_cursor=next_cursor,
    )


__all__ = [
    "DEDUPE_WINDOWS",
    "DEFAULT_CONFIG",
    "DEFAULT_DEDUPE_WINDOW",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "PAYLOAD_BY_TYPE",
    "BandThresholds",
    "CandidateInput",
    "ConfidenceBand",
    "ConfidenceConfig",
    "ConfidenceWeights",
    "EmbeddingDimMismatchError",
    "ScoreResult",
    "SignalPage",
    "SignalPayload",
    "SignalRead",
    "SignalType",
    "SignalValidationError",
    "backfill_embeddings",
    "build_embedding_text",
    "compute_dedupe_hash",
    "compute_dedupe_hash_for_payload",
    "config_from_recipe",
    "decode_cursor",
    "dedupe_key_for_candidate",
    "embed_signals",
    "embedding_text_for_signal",
    "encode_cursor",
    "find_duplicate",
    "get_signal",
    "list_signals",
    "merge_signal",
    "parse_signal_payload",
    "promote_candidate_to_signal",
    "score_candidate_confidence",
    "store_signal",
    "window_for",
]
