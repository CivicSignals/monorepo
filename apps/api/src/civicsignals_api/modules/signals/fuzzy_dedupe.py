"""Embedding-based fuzzy deduplication for high-stakes signal types (doc 19 §7.4; E10).

After exact-match dedupe (E5, :mod:`signals.dedupe`) finds no duplicate, the fuzzy
layer runs an ANN cosine-similarity query over ``signals_signal.vector_embedding``
for the two high-stakes signal types: ``rfp_posted`` and ``contract_expiring``.

Design (§7.4):
- Embed the candidate's ``title + summary`` (reusing I1's
  :func:`~signals.embedding.build_embedding_text` so the text is identical to the
  stored embedding text of any prior signal).
- ANN-query the pgvector index (``ivfflat cosine``; index built by I1) for existing
  signals of the **same entity + signal_type within the type window** at ≥ 0.92
  cosine similarity.
- **Review gate then auto-merge**: the first 100 fuzzy matches per signal type go to
  a ``signals_fuzzy_review`` table (``status=pending``) rather than auto-merging, so
  a human can validate the 0.92 threshold. Once a type has accumulated
  ``GRADUATION_COUNT`` reviewed (approved or rejected) entries its subsequent fuzzy
  matches above threshold are auto-merged.
- Config: ``FUZZY_COSINE_THRESHOLD`` (0.92) and ``GRADUATION_COUNT`` (100) are
  module-level constants, config-injectable via :class:`FuzzyDedupeConfig`.

Responsibilities:
- :func:`is_high_stakes_type` — gate by signal type (called from the dedupe path).
- :func:`find_fuzzy_duplicate` — ANN query for the nearest above-threshold signal.
- :func:`should_auto_merge` — check whether a type has graduated past human review.
- :func:`create_fuzzy_review` — write a ``signals_fuzzy_review`` row.
- :func:`apply_fuzzy_review` — approve (merge) or reject a review row.
- :class:`FuzzyDedupeConfig` — injectable config (threshold + graduation count).

Cross-module invariants (doc 06 §3):
- Only ``signals.services`` calls this module's functions — the extraction pipeline
  never imports here directly.
- ``merge_signal`` from :mod:`signals.dedupe` is reused for the auto-merge path
  (doc 19 §7.4 says "treat as fuzzy duplicate", which means the same merge semantics).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.llm_gateway import LLMGateway, get_gateway

from .dedupe import merge_signal, window_for
from .embedding import build_embedding_text
from .models import EMBEDDING_DIM, SIGNAL_STATUS_MERGED, Signal
from .models_fuzzy_review import (
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    SignalFuzzyReview,
)
from .schemas import SignalType

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Cosine similarity floor for a fuzzy match (doc 19 §7.4). Signals within this
#: distance are "near-duplicates"; below it they are distinct. 0.92 is the
#: documented MVP starting threshold — tuned from the first 100 human-reviewed
#: matches per type (§7.4 + §13.5).
FUZZY_COSINE_THRESHOLD: Final = 0.92

#: Number of human-reviewed fuzzy matches required before a signal type graduates
#: to **auto-merge** (doc 19 §7.4: "first 100 … then auto-merge"). Only reviewed
#: (approved or rejected) rows count toward the total — pending rows do not.
GRADUATION_COUNT: Final = 100

#: The high-stakes signal types for which fuzzy dedupe runs (doc 19 §7.4).
HIGH_STAKES_TYPES: frozenset[SignalType] = frozenset(
    {SignalType.RFP_POSTED, SignalType.CONTRACT_EXPIRING}
)


@dataclass(frozen=True)
class FuzzyDedupeConfig:
    """Injectable configuration for the fuzzy-dedupe path (E10, doc 19 §7.4).

    All defaults reflect the §7.4 spec. Tests + a future recipe-level override can
    supply different values without touching the module constants.

    ``threshold``:   cosine similarity floor (0-1; higher = more conservative).
    ``graduation``:  number of reviewed rows before auto-merge is enabled.
    ``high_stakes``: the signal types eligible for fuzzy dedupe.
    """

    threshold: float = FUZZY_COSINE_THRESHOLD
    graduation: int = GRADUATION_COUNT
    high_stakes: frozenset[SignalType] = field(default_factory=lambda: HIGH_STAKES_TYPES)


DEFAULT_FUZZY_CONFIG: FuzzyDedupeConfig = FuzzyDedupeConfig()

# ---------------------------------------------------------------------------
# Type gate
# ---------------------------------------------------------------------------


def is_high_stakes_type(
    signal_type: SignalType,
    config: FuzzyDedupeConfig = DEFAULT_FUZZY_CONFIG,
) -> bool:
    """Whether this signal type runs the embedding-based fuzzy dedupe (doc 19 §7.4)."""
    return signal_type in config.high_stakes


# ---------------------------------------------------------------------------
# ANN query — find a near-duplicate within the type window
# ---------------------------------------------------------------------------


async def find_fuzzy_duplicate(
    session: AsyncSession,
    *,
    embedding: list[float],
    signal_type: SignalType,
    entity_id: uuid.UUID | None,
    exclude_signal_id: uuid.UUID | None = None,
    now: datetime | None = None,
    config: FuzzyDedupeConfig = DEFAULT_FUZZY_CONFIG,
) -> Signal | None:
    """ANN-query for an existing signal within cosine threshold (doc 19 §7.4).

    Queries ``signals_signal`` for rows of the same entity + signal_type within the
    type-specific window (reusing :func:`~signals.dedupe.window_for`) whose
    ``vector_embedding`` is within ``config.threshold`` cosine similarity. Returns
    the *closest* match above the threshold (lowest cosine distance = highest
    similarity), or ``None`` if no match exists or the candidate's embedding was not
    computed.

    The ANN query uses the ivfflat index (built by I1) via pgvector's
    ``<=>`` cosine-distance operator. ``1 - cosine_distance ≥ threshold`` is
    equivalent to ``cosine_distance ≤ 1 - threshold``.

    ``exclude_signal_id`` — when provided, the row with that id is excluded from the
    results. Pass the candidate signal's id so the ANN query cannot return the
    candidate row itself (distance 0), which would make the match non-deterministic
    and mask real nearest neighbours.

    Requires the candidate's embedding to be pre-computed (non-empty list of floats);
    returns ``None`` immediately if the embedding is empty (best-effort: a failed
    embed leaves the fuzzy path skipped, which is the documented behaviour).
    """
    if not embedding:
        return None

    now = now or datetime.now(UTC)
    cutoff = now - window_for(signal_type)
    effective_date = func.coalesce(Signal.occurred_at, Signal.created_at)

    # pgvector cosine distance: 0 = identical, 2 = maximally opposite (unit vectors).
    # threshold 0.92 similarity → distance ≤ 0.08.
    max_distance = 1.0 - config.threshold

    # pgvector cosine_distance accepts a Python list[float] directly — the same
    # pattern used by the smart-search ANN query (I3). No explicit cast needed.
    filters = [
        Signal.signal_type == signal_type.value,
        Signal.entity_id == entity_id,  # IS NULL when entity_id is None
        effective_date > cutoff,
        Signal.vector_embedding.isnot(None),
        Signal.vector_embedding.cosine_distance(embedding) <= max_distance,
    ]
    if exclude_signal_id is not None:
        filters.append(Signal.id != exclude_signal_id)

    stmt = (
        select(Signal)
        .where(and_(*filters))
        .order_by(Signal.vector_embedding.cosine_distance(embedding))
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


# ---------------------------------------------------------------------------
# Review gate — should this match auto-merge?
# ---------------------------------------------------------------------------


async def should_auto_merge(
    session: AsyncSession,
    signal_type: SignalType,
    config: FuzzyDedupeConfig = DEFAULT_FUZZY_CONFIG,
) -> bool:
    """Whether this signal type has graduated to auto-merge (doc 19 §7.4).

    Returns True once ``config.graduation`` reviewed (approved or rejected) fuzzy-
    review rows exist for the type. Pending rows (awaiting human decision) are
    excluded — only decided rows count toward the graduation threshold, because
    pending rows represent "we don't know yet."
    """
    reviewed_count = await session.scalar(
        select(func.count())
        .select_from(SignalFuzzyReview)
        .where(
            and_(
                SignalFuzzyReview.signal_type == signal_type.value,
                SignalFuzzyReview.status != REVIEW_STATUS_PENDING,
            )
        )
    )
    return int(reviewed_count or 0) >= config.graduation


# ---------------------------------------------------------------------------
# Review row creation
# ---------------------------------------------------------------------------


async def create_fuzzy_review(
    session: AsyncSession,
    *,
    candidate_signal_id: uuid.UUID,
    matched_signal_id: uuid.UUID,
    similarity: float,
    signal_type: SignalType,
) -> SignalFuzzyReview:
    """Insert a ``signals_fuzzy_review`` row for human validation (doc 19 §7.4).

    Called when a fuzzy match is found for a type that has not yet graduated. The
    candidate signal is stored (as a pending signal, status=new) but NOT merged into
    the matched signal until a human approves. Returns the new review row.
    """
    row = SignalFuzzyReview(
        id=uuid.uuid4(),
        candidate_signal_id=candidate_signal_id,
        matched_signal_id=matched_signal_id,
        similarity=round(similarity, 6),
        signal_type=signal_type.value,
        status=REVIEW_STATUS_PENDING,
    )
    session.add(row)
    await session.flush()
    log.info(
        "signals.fuzzy_dedupe.review_created",
        review_id=str(row.id),
        signal_type=signal_type.value,
        candidate_signal_id=str(candidate_signal_id),
        matched_signal_id=str(matched_signal_id),
        similarity=similarity,
    )
    return row


# ---------------------------------------------------------------------------
# Review approval / rejection
# ---------------------------------------------------------------------------


async def apply_fuzzy_review(
    session: AsyncSession,
    review_id: uuid.UUID,
    *,
    approved: bool,
    reviewer_note: str | None = None,
    now: datetime | None = None,
) -> SignalFuzzyReview:
    """Approve or reject a pending fuzzy-review row (doc 19 §7.4).

    **Approve**: merge the candidate signal into the matched signal (same merge
    semantics as exact-match dedupe: doc 19 §7.3, reuses :func:`~signals.dedupe.merge_signal`).
    The candidate signal row is left in place after merge (it may be referenced by
    ``source_candidate_id``); its source documents are folded into the matched signal.

    **Reject**: the candidate remains as an independent signal; the review row is
    stamped ``rejected``.

    Returns the updated review row. Raises :class:`FuzzyReviewNotFoundError` if the
    review does not exist, :class:`FuzzyReviewAlreadyDecidedError` if it is not
    pending.
    """
    now = now or datetime.now(UTC)
    review = await session.get(SignalFuzzyReview, review_id)
    if review is None:
        raise FuzzyReviewNotFoundError(review_id)
    if review.status != REVIEW_STATUS_PENDING:
        raise FuzzyReviewAlreadyDecidedError(review_id, review.status)

    if approved:
        candidate = await session.get(Signal, review.candidate_signal_id)
        matched = await session.get(Signal, review.matched_signal_id)
        if candidate is None or matched is None:
            # One or both signal rows were deleted after the review was created.
            # Raising here prevents the review from being marked approved without
            # actually merging — the review queue's integrity is more important than
            # silently accepting a no-op approval.
            raise FuzzyReviewSignalMissingError(
                review_id, review.candidate_signal_id, review.matched_signal_id
            )
        merge_signal(
            matched,
            new_doc_ids=[uuid.UUID(s) for s in candidate.raw_document_ids],
            new_confidence=candidate.confidence,
            now=now,
        )
        # Soft-delete the candidate: mark it ``merged`` so it no longer surfaces in
        # list_signals / feed queries. The row is kept for the audit trail (its
        # raw_document_ids have been folded into the surviving signal above).
        candidate.status = SIGNAL_STATUS_MERGED
        candidate.merged_into = matched.id
        await session.flush()
        review.status = REVIEW_STATUS_APPROVED
        log.info(
            "signals.fuzzy_dedupe.review_approved",
            review_id=str(review_id),
            matched_signal_id=str(review.matched_signal_id),
            candidate_signal_id=str(review.candidate_signal_id),
        )
    else:
        review.status = REVIEW_STATUS_REJECTED
        log.info(
            "signals.fuzzy_dedupe.review_rejected",
            review_id=str(review_id),
        )

    review.reviewed_at = now
    review.reviewer_note = reviewer_note
    await session.flush()
    return review


# ---------------------------------------------------------------------------
# High-level entry point: run fuzzy dedupe for one candidate
# ---------------------------------------------------------------------------


async def run_fuzzy_dedupe(
    session: AsyncSession,
    *,
    candidate_signal: Signal,
    signal_type: SignalType,
    new_doc_ids: list[uuid.UUID],
    new_confidence: float | None,
    gateway: LLMGateway | None = None,
    workspace_id: str | None = None,
    config: FuzzyDedupeConfig = DEFAULT_FUZZY_CONFIG,
    now: datetime | None = None,
) -> FuzzyDedupeResult:
    """Run embedding-based fuzzy dedupe for one signal after exact-match found nothing.

    Called by ``signals.services.promote_candidate_to_signal`` (the exact-key path
    of doc 19 §7.2 returned nothing; we are now in §7.4 territory). The candidate
    signal has already been inserted as a new signal row (with a new id) and flushed;
    this function either:

    - finds no fuzzy match → returns ``matched=False`` (the new signal stands);
    - finds a match, type not yet graduated → creates a review row and returns
      ``matched=True, routed_to_review=True`` (human decides later);
    - finds a match, type graduated → auto-merges into the matched signal and returns
      ``matched=True, routed_to_review=False, auto_merged=True``.

    The embedding of the candidate is computed on demand if ``candidate_signal.vector_embedding``
    is ``None`` — the extraction pipeline may or may not have embedded it yet at the
    time this runs. A gateway error leaves the embedding NULL and skips fuzzy dedupe
    (best-effort: the signal is never lost).
    """
    now = now or datetime.now(UTC)
    gateway = gateway or get_gateway()

    # 1. Obtain the candidate's embedding (compute if missing).
    embedding = await _ensure_embedding(
        session, candidate_signal, gateway=gateway, workspace_id=workspace_id
    )
    if embedding is None:
        # Embed failed (best-effort) — fuzzy path skipped; the new signal stands.
        return FuzzyDedupeResult(matched=False)

    # 2. ANN query for a near-duplicate within the type window.
    # Exclude the candidate's own row so it cannot appear as its own nearest
    # neighbour (distance 0), which would mask the real nearest match.
    matched_signal = await find_fuzzy_duplicate(
        session,
        embedding=embedding,
        signal_type=signal_type,
        entity_id=candidate_signal.entity_id,
        exclude_signal_id=candidate_signal.id,
        now=now,
        config=config,
    )
    if matched_signal is None:
        return FuzzyDedupeResult(matched=False)

    # Compute the actual similarity for the review row.
    similarity = _cosine_similarity(embedding, matched_signal.vector_embedding or [])

    log.info(
        "signals.fuzzy_dedupe.match_found",
        signal_type=signal_type.value,
        candidate_signal_id=str(candidate_signal.id),
        matched_signal_id=str(matched_signal.id),
        similarity=similarity,
    )

    # 3. Route: review gate or auto-merge?
    auto_merge = await should_auto_merge(session, signal_type, config=config)
    if not auto_merge:
        # First ≤ graduation matches per type → human review queue.
        review = await create_fuzzy_review(
            session,
            candidate_signal_id=candidate_signal.id,
            matched_signal_id=matched_signal.id,
            similarity=similarity,
            signal_type=signal_type,
        )
        return FuzzyDedupeResult(
            matched=True,
            matched_signal_id=matched_signal.id,
            similarity=similarity,
            routed_to_review=True,
            review_id=review.id,
        )

    # Auto-merge: fold the new evidence into the surviving signal.
    merge_signal(
        matched_signal,
        new_doc_ids=new_doc_ids,
        new_confidence=new_confidence,
        now=now,
    )
    # Soft-delete the candidate: mark it ``merged`` so it no longer surfaces in
    # list_signals / feed queries. The row is kept for the audit trail (its
    # raw_document_ids have been folded into the surviving signal above).
    candidate_signal.status = SIGNAL_STATUS_MERGED
    candidate_signal.merged_into = matched_signal.id
    await session.flush()
    log.info(
        "signals.fuzzy_dedupe.auto_merged",
        signal_type=signal_type.value,
        candidate_signal_id=str(candidate_signal.id),
        matched_signal_id=str(matched_signal.id),
        similarity=similarity,
    )
    return FuzzyDedupeResult(
        matched=True,
        matched_signal_id=matched_signal.id,
        similarity=similarity,
        routed_to_review=False,
        auto_merged=True,
    )


async def _ensure_embedding(
    session: AsyncSession,
    signal: Signal,
    *,
    gateway: LLMGateway,
    workspace_id: str | None,
) -> list[float] | None:
    """Return the signal's embedding, computing it on demand if NULL (E10, best-effort)."""
    if signal.vector_embedding is not None:
        return list(signal.vector_embedding)
    # Compute inline for the fuzzy-dedupe path — we need the vector now.
    embed_text = build_embedding_text(
        title=signal.title,
        summary=signal.summary,
        details=signal.details,
    )
    try:
        result = await gateway.embed([embed_text], workspace_id=workspace_id)
        if result.dim != EMBEDDING_DIM:
            log.warning(
                "signals.fuzzy_dedupe.embed_dim_mismatch",
                got=result.dim,
                expected=EMBEDDING_DIM,
            )
            return None
        vector = result.vectors[0]
        signal.vector_embedding = vector
        await session.flush()
        return vector
    except Exception as exc:
        log.warning(
            "signals.fuzzy_dedupe.embed_failed",
            signal_id=str(signal.id),
            error=str(exc),
        )
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two L2-normalised vectors (doc 19 §7.4).

    The embeddings from the gateway are L2-normalised (unit vectors), so the cosine
    similarity is the dot product. Falls back to 0.0 on a length mismatch or empty
    inputs (should not happen in practice — both vectors come from the same model).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FuzzyDedupeResult:
    """Outcome of a :func:`run_fuzzy_dedupe` call.

    ``matched``: a near-duplicate was found (True) or the signal is distinct (False).
    ``matched_signal_id``: the surviving signal's id (populated when ``matched``).
    ``similarity``: the cosine similarity at which the match was found (0 when not matched).
    ``routed_to_review``: the match went to the human review queue (not yet merged).
    ``review_id``: the ``signals_fuzzy_review.id`` created (when ``routed_to_review``).
    ``auto_merged``: the candidate was merged automatically after type graduation.
    """

    matched: bool = False
    matched_signal_id: uuid.UUID | None = None
    similarity: float = 0.0
    routed_to_review: bool = False
    review_id: uuid.UUID | None = None
    auto_merged: bool = False


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class FuzzyReviewNotFoundError(Exception):
    """No ``signals_fuzzy_review`` row exists for the given id."""

    def __init__(self, review_id: uuid.UUID) -> None:
        super().__init__(f"no fuzzy review with id {review_id}")
        self.review_id = review_id


class FuzzyReviewAlreadyDecidedError(Exception):
    """The review row is not in ``pending`` status (already approved or rejected)."""

    def __init__(self, review_id: uuid.UUID, status: str) -> None:
        super().__init__(f"fuzzy review {review_id} already decided: status={status!r}")
        self.review_id = review_id
        self.current_status = status


class FuzzyReviewSignalMissingError(Exception):
    """One or both signal rows referenced by a review no longer exist.

    Raised when approving a review whose candidate or matched signal row has been
    deleted between review creation and approval. The review is left as ``pending``
    so it can be investigated rather than being silently marked approved with no merge.
    """

    def __init__(
        self,
        review_id: uuid.UUID,
        candidate_signal_id: uuid.UUID,
        matched_signal_id: uuid.UUID,
    ) -> None:
        super().__init__(
            f"fuzzy review {review_id}: signal row(s) missing "
            f"(candidate={candidate_signal_id}, matched={matched_signal_id})"
        )
        self.review_id = review_id
        self.candidate_signal_id = candidate_signal_id
        self.matched_signal_id = matched_signal_id


__all__ = [
    "DEFAULT_FUZZY_CONFIG",
    "FUZZY_COSINE_THRESHOLD",
    "GRADUATION_COUNT",
    "HIGH_STAKES_TYPES",
    "FuzzyDedupeConfig",
    "FuzzyDedupeResult",
    "FuzzyReviewAlreadyDecidedError",
    "FuzzyReviewNotFoundError",
    "FuzzyReviewSignalMissingError",
    "apply_fuzzy_review",
    "create_fuzzy_review",
    "find_fuzzy_duplicate",
    "is_high_stakes_type",
    "run_fuzzy_dedupe",
    "should_auto_merge",
]
