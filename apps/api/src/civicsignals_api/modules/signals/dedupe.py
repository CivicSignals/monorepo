"""Exact-match signal deduplication (doc 19 §7; E5).

Stage 6 of the processing funnel (doc 19 §1, §7): before a validated candidate is
written as a new ``signals_signal`` row, we check whether it is a duplicate of a
signal we already have *within a type-specific time window* — and if so **merge**
the new evidence into the surviving signal rather than inserting a duplicate.

The dedupe key (doc 19 §7.1) is a hash of ``entity_id + signal_type +
normalized_key_fields``, where the key fields differ per signal type (an RFP keys
on title + due date; a budget on fiscal year + category; a leadership change on
role + person; …). The window also differs per type — procurement cycles vary, so
a 90-day RFP window and a 730-day leadership window are very different statements
about "is this the same event".

This module is pure key/window logic + the windowed lookup-and-merge over the
table. It is the seam ``signals.services`` calls from its store path and the seam
``extraction.pipeline`` drives via the ``dedupe_candidate`` stage. The
embedding-based **fuzzy** fallback (doc 19 §7.4) is out of scope here — that is
E10/I1; this is the exact-key half (doc 19 §7.1-§7.3).

Note on the unique index: E4 declared ``signals_dedupe_idx`` *unique* on
``(entity_id, signal_type, content_hash)`` with no time component, which would
forbid the same key recurring after its window (doc 19 §7.1 says that *is* a new
signal — e.g. the same annual RFP a year later). E5 relaxes that to a non-unique
windowed lookup index (``signals_dedupe_window_idx``) and moves the
"one-signal-per-window" guarantee into :func:`find_duplicate` here, which is the
doc 19 §7.2 SELECT-then-INSERT-or-merge process.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Signal
from .schemas import SignalPayload, SignalType

# ---------------------------------------------------------------------------
# Per-type dedupe windows (doc 19 §7.1)
# ---------------------------------------------------------------------------

# How far back a matching key still counts as the *same* signal, per type. Beyond
# the window the same key is a genuinely new signal (a fresh procurement cycle, a
# re-posted role). These differ because procurement cycles differ (doc 19 §7.1,
# §13.3 — the windows are tuned per type after seeing real distributions).
DEDUPE_WINDOWS: dict[SignalType, timedelta] = {
    SignalType.RFP_POSTED: timedelta(days=90),
    SignalType.CONTRACT_EXPIRING: timedelta(days=365),
    SignalType.CONTRACT_AWARDED: timedelta(days=365),
    SignalType.BUDGET_APPROVED: timedelta(days=365),
    SignalType.LEADERSHIP_CHANGE: timedelta(days=730),
    SignalType.BOARD_AGENDA_ITEM: timedelta(days=60),
}

# Default window for signal types not listed above (rfi_rfq, grant_*, open_job,
# news_mention, strategic_plan_published). 90 days mirrors the documented RFP
# window — a sane "same recent thing" default until each type earns its own tuning.
# # TODO E5+: recipe-configurable per-type window overrides (doc 19 §13.3); the
# windows are guesses until real distributions are seen.
DEFAULT_DEDUPE_WINDOW: timedelta = timedelta(days=90)


def window_for(signal_type: SignalType) -> timedelta:
    """The dedupe lookback window for a signal type (doc 19 §7.1)."""
    return DEDUPE_WINDOWS.get(signal_type, DEFAULT_DEDUPE_WINDOW)


# ---------------------------------------------------------------------------
# Canonical key-field normalization (doc 19 §7.1)
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(value: object) -> str:
    """Normalize a free-text key field: casefold + collapse whitespace (doc 19 §7.1).

    ``normalized(title)`` / ``normalized(topic)`` in the dedup-key table mean the
    same title with different casing/spacing hashes identically, so the three
    sightings of one RFP (portal, newspaper, the entity's own site) collapse to one
    signal (doc 19 §7.3). Punctuation is left intact — exact-key dedupe is
    deliberately conservative; near-but-not-identical titles are the fuzzy case
    (doc 19 §7.4, E10) and must *not* collapse here.
    """
    text = "" if value is None else str(value)
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


def _date_only(value: object) -> str:
    """Reduce a date/datetime-ish key field to a ``YYYY-MM-DD`` string (doc 19 §7.1).

    The dedup-key table keys RFPs on ``due_at (date only)`` and agenda items on
    ``meeting_date`` — the calendar day, not the timestamp, so two sightings that
    differ only by an extracted time-of-day still dedupe. Accepts ``datetime`` /
    ``date`` and ISO-8601 strings (the payload's ``model_dump(mode="json")`` form);
    anything unparseable falls back to its normalized string so the field still
    contributes to the key rather than silently vanishing.
    """
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = _normalize_text(value)
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("z", "+00:00")).date().isoformat()
    except ValueError:
        # Already a date-only ISO string, or unparseable — keep the first 10 chars
        # (``YYYY-MM-DD``) when it looks date-like, else the raw normalized text.
        return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else text


def _key_fields(signal_type: SignalType, fields: Mapping[str, object]) -> list[str]:
    """The ordered, normalized canonical key fields for a signal type (doc 19 §7.1).

    Maps each type to the columns in the doc 19 §7.1 dedup-key table. Types not in
    the table key on ``normalized(title)`` — the one field every payload has — so
    every type produces a stable, content-bearing key (the funnel never silently
    skips dedupe). The list is ordered so the joined hash basis is deterministic.
    """
    if signal_type is SignalType.RFP_POSTED:
        return [_normalize_text(fields.get("title")), _date_only(fields.get("due_at"))]
    if signal_type is SignalType.CONTRACT_EXPIRING:
        # The doc keys on ``vendor_id``; entity/vendor resolution (E10) is not wired
        # yet, so we key on the normalized vendor *name* + expiry. When E10 resolves
        # vendors this becomes the canonical vendor id.
        return [
            _normalize_text(fields.get("vendor_name")),
            _date_only(fields.get("expires_at")),
        ]
    if signal_type is SignalType.CONTRACT_AWARDED:
        return [
            _normalize_text(fields.get("vendor_name")),
            _date_only(fields.get("awarded_at")),
        ]
    if signal_type is SignalType.BUDGET_APPROVED:
        return [
            _normalize_text(fields.get("fiscal_year")),
            _normalize_text(fields.get("category")),
        ]
    if signal_type is SignalType.LEADERSHIP_CHANGE:
        return [
            _normalize_text(fields.get("role")),
            _normalize_text(fields.get("person_name")),
        ]
    if signal_type is SignalType.BOARD_AGENDA_ITEM:
        return [
            _date_only(fields.get("meeting_date")),
            _normalize_text(fields.get("topic")),
        ]
    # Default (rfi_rfq, grant_*, open_job, news_mention, strategic_plan_published):
    # key on the title alone — exact-key dedupe collapses re-fetches of the same
    # listing; anything subtler is the fuzzy case (doc 19 §7.4).
    return [_normalize_text(fields.get("title"))]


def compute_dedupe_hash(
    signal_type: SignalType,
    entity_id: uuid.UUID | None,
    fields: Mapping[str, object],
) -> str:
    """Compute the canonical exact-match dedupe hash (doc 19 §7.1).

    Hashes ``entity_id + signal_type + normalized_key_fields`` into a hex SHA-256
    digest (the ``content_hash`` column). ``entity_id`` is part of the basis so the
    same title under two different districts never collides; a ``None`` entity_id
    (resolution pending, doc 19 §4.3) hashes to a stable sentinel so two pending
    sightings of the same thing still dedupe. The key fields are joined with a NUL
    separator so adjacent fields cannot merge into an ambiguous basis.
    """
    parts = [
        "" if entity_id is None else str(entity_id),
        signal_type.value,
        *_key_fields(signal_type, fields),
    ]
    basis = "\x00".join(parts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def compute_dedupe_hash_for_payload(
    payload: SignalPayload,
    entity_id: uuid.UUID | None,
) -> str:
    """Compute the dedupe hash from a validated typed payload (doc 19 §7.1).

    Convenience wrapper used by the store path: serializes the payload to its JSON
    field dict (the same shape stored in ``details_jsonb``) and hashes it. Using the
    validated payload (not the raw candidate fields) means the key is computed over
    coerced, schema-valid values — e.g. a ``due_at`` that is a real ``datetime``.
    """
    return compute_dedupe_hash(payload.signal_type, entity_id, payload.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Windowed lookup + merge (doc 19 §7.2, §7.3)
# ---------------------------------------------------------------------------


async def find_duplicate(
    session: AsyncSession,
    *,
    entity_id: uuid.UUID | None,
    signal_type: SignalType,
    dedupe_hash: str,
    now: datetime | None = None,
) -> Signal | None:
    """Find an existing signal duplicating this key within the type window (§7.2).

    The doc 19 §7.2 SELECT: ``WHERE (entity_id, signal_type, content_hash) match AND
    coalesce(occurred_at, created_at) > now() - window``. We window on the *event*
    date (``occurred_at``) when known, falling back to when we stored the row
    (``created_at``) — the procurement-cycle window is about when the thing happened,
    not when we ingested it, but a signal without an ``occurred_at`` still needs a
    timeline. Backed by ``signals_dedupe_window_idx``. Returns the most-recent match
    so a merge accretes onto the freshest surviving signal.
    """
    now = now or datetime.now(UTC)
    cutoff = now - window_for(signal_type)
    effective_date = func.coalesce(Signal.occurred_at, Signal.created_at)
    stmt = (
        select(Signal)
        .where(
            Signal.signal_type == signal_type.value,
            Signal.content_hash == dedupe_hash,
            Signal.entity_id == entity_id,  # ``== None`` -> ``IS NULL`` (intended)
            effective_date > cutoff,
        )
        .order_by(effective_date.desc(), Signal.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


def merge_signal(
    existing: Signal,
    *,
    new_doc_ids: list[uuid.UUID],
    new_confidence: float | None,
    now: datetime | None = None,
) -> Signal:
    """Merge new evidence into a surviving duplicate signal (doc 19 §7.3).

    The merged signal keeps its id and feed position but is *backed by more
    evidence* (doc 19 §7.3): every corroborating ``raw_document_id`` is appended
    (order-preserving, de-duplicated — **no source document is lost**), the higher
    ``extraction_confidence`` of the two is kept, and ``observed_at`` (our last-seen
    timestamp) advances to now. The full per-field fill-in (doc 19 §7.3 "fields the
    existing signal lacks") is a later refinement; preserving sources + confidence +
    last-seen is the load-bearing E5 behaviour and is what the audit panel shows.
    """
    now = now or datetime.now(UTC)
    merged: list[str] = []
    seen: set[str] = set()
    for sid in [*existing.raw_document_ids, *(str(d) for d in new_doc_ids)]:
        if sid not in seen:
            seen.add(sid)
            merged.append(sid)
    existing.raw_document_ids = merged
    if new_confidence is not None and (
        existing.confidence is None or new_confidence > existing.confidence
    ):
        existing.confidence = new_confidence
    # ``observed_at`` is our last-seen marker (doc 19 §7.3 "update last_seen_at").
    existing.observed_at = now
    return existing


__all__ = [
    "DEDUPE_WINDOWS",
    "DEFAULT_DEDUPE_WINDOW",
    "compute_dedupe_hash",
    "compute_dedupe_hash_for_payload",
    "find_duplicate",
    "merge_signal",
    "window_for",
]
