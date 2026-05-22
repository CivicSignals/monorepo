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
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

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

# Pagination defaults (doc 06 §5, doc 08 §1.5).
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

# Confidence band below which a promoted signal is held for review (doc 19 §6.3:
# the 0.4-0.6 ``pending_review`` band). Above it, the signal is stored ``new``.
# # TODO E6: the real per-recipe-configurable thresholds + degraded flag come with
# the confidence blend; E4 applies the documented default band here.
REVIEW_CONFIDENCE_FLOOR = 0.6


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
    2. **Upsert** on the dedupe key ``(entity_id, signal_type, content_hash)``
       (doc 07 ``signals_dedupe_idx``): a first sighting INSERTs; a re-promotion of
       the same candidate (idempotent replay, doc 19 §1) appends the new
       ``raw_document_id`` to the existing row's ``raw_document_ids`` and keeps the
       higher confidence (the doc 19 §7.3 merge skeleton).

    The caller owns the transaction (this flushes, not commits).

    # TODO E5: real per-type dedup key + windowed lookup + full merge logic (doc 19
    # §7). E4 upserts on the exact key only — fuzzy/embedding dedupe is E5/I1.
    # TODO E6: the real confidence blend + recipe-configurable bands (doc 19 §6.2-3);
    # E4 applies the documented default ``pending_review`` band below.
    """
    payload = parse_signal_payload(candidate.signal_type, candidate.fields)
    return await store_signal(session, payload, candidate)


async def store_signal(
    session: AsyncSession,
    payload: SignalPayload,
    candidate: CandidateInput,
) -> Signal:
    """Upsert a validated payload into ``signals_signal`` (doc 07 §2, doc 19 §6.1).

    Separated from :func:`promote_candidate_to_signal` so a caller that already
    holds a validated :class:`SignalPayload` (e.g. a backfill or a test) can store
    it directly. Upserts on the dedupe key; the caller commits.
    """
    review = _needs_review(candidate)
    details = payload.model_dump(mode="json")
    raw_doc_ids = _ordered_unique([candidate.raw_document_id, *candidate.extra_raw_document_ids])

    values = {
        "id": uuid.uuid4(),
        "entity_id": candidate.entity_id,
        "entity_name_raw": candidate.entity_name,
        "signal_type": payload.signal_type.value,
        "recipe_id": candidate.recipe_id,
        "extraction_job_id": candidate.extraction_job_id,
        "source_candidate_id": candidate.source_candidate_id,
        "raw_document_ids": [str(d) for d in raw_doc_ids],
        "content_hash": candidate.content_hash,
        "occurred_at": candidate.occurred_at,
        "observed_at": datetime.now(UTC),
        "title": payload.title,
        "summary": payload.summary,
        "details": details,
        "confidence": candidate.confidence,
        "status": SIGNAL_STATUS_PENDING_REVIEW if review else SIGNAL_STATUS_NEW,
        "review_required": review,
    }

    # Upsert on the dedupe unique index (doc 07 ``signals_dedupe_idx``). A conflict
    # means we have already seen this exact signal — append the new corroborating
    # document and keep the higher confidence rather than inserting a duplicate
    # (the doc 19 §7.3 merge skeleton; full merge is E5).
    stmt = (
        pg_insert(Signal)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["entity_id", "signal_type", "content_hash"])
        .returning(Signal.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()

    if inserted_id is not None:
        row = await session.get(Signal, inserted_id)
        assert row is not None
        return row

    # Conflict: the signal already exists — merge this evidence into it (doc 19 §7.3).
    existing = await _find_by_dedupe_key(
        session, candidate.entity_id, payload.signal_type.value, candidate.content_hash
    )
    assert existing is not None  # the conflicting row must exist post-insert
    _merge_evidence(existing, raw_doc_ids, candidate.confidence)
    await session.flush()
    return existing


def _needs_review(candidate: CandidateInput) -> bool:
    """Whether a promoted signal is held for review (doc 19 §6.3, §4.3).

    Two documented triggers: (a) a low extraction confidence in the 0.4-0.6
    ``pending_review`` band (doc 19 §6.3) — a ``None`` confidence is treated as not
    triggering review on its own; (b) entity resolution pending (doc 19 §4.3: no
    resolved ``entity_id``).
    """
    if candidate.entity_id is None:
        return True
    return candidate.confidence is not None and candidate.confidence < REVIEW_CONFIDENCE_FLOOR


def _ordered_unique(ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """De-duplicate a list of ids preserving first-seen order."""
    seen: set[uuid.UUID] = set()
    out: list[uuid.UUID] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _merge_evidence(
    existing: Signal, new_doc_ids: list[uuid.UUID], new_confidence: float | None
) -> None:
    """Append corroborating docs + keep the higher confidence (doc 19 §7.3 skeleton)."""
    # raw_document_ids is a JSONB array of UUID strings; append the new ones,
    # preserving order and de-duplicating, then store back as strings.
    merged: list[str] = []
    seen: set[str] = set()
    for sid in [*existing.raw_document_ids, *[str(d) for d in new_doc_ids]]:
        if sid not in seen:
            seen.add(sid)
            merged.append(sid)
    existing.raw_document_ids = merged
    if new_confidence is not None and (
        existing.confidence is None or new_confidence > existing.confidence
    ):
        existing.confidence = new_confidence


async def _find_by_dedupe_key(
    session: AsyncSession,
    entity_id: uuid.UUID | None,
    signal_type: str,
    content_hash: str,
) -> Signal | None:
    stmt = select(Signal).where(
        Signal.signal_type == signal_type,
        Signal.content_hash == content_hash,
    )
    # entity_id is part of the key and may be NULL; ``== None`` -> ``IS NULL``.
    stmt = stmt.where(Signal.entity_id == entity_id)
    return (await session.execute(stmt)).scalar_one_or_none()


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
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "PAYLOAD_BY_TYPE",
    "CandidateInput",
    "SignalPage",
    "SignalPayload",
    "SignalRead",
    "SignalType",
    "SignalValidationError",
    "decode_cursor",
    "encode_cursor",
    "get_signal",
    "list_signals",
    "parse_signal_payload",
    "promote_candidate_to_signal",
    "store_signal",
]
