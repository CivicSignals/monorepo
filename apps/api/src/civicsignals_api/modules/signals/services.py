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

E10 adds:

- :func:`store_signal` now calls :func:`~signals.fuzzy_dedupe.run_fuzzy_dedupe`
  after exact-match finds nothing, for high-stakes types (``rfp_posted``,
  ``contract_expiring``) — doc 19 §7.4.
- Fuzzy-dedupe helpers (:func:`get_fuzzy_review`, :func:`list_fuzzy_reviews`,
  :func:`decide_fuzzy_review`) exposed through this public surface so the routes
  module and any cross-module caller uses this seam only.

Cursor pagination uses the same keyset strategy as the other modules (doc 06 §5):
the UUID v7 id is time-ordered, so ``observed_at DESC, id DESC`` is a stable feed
order and a base64-encoded id is the opaque cursor.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.ids import uuid7
from civicsignals_api.modules.entities.models import Entity
from civicsignals_api.modules.icp.models import IcpDefinition

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
from .fuzzy_dedupe import (
    DEFAULT_FUZZY_CONFIG,
    FUZZY_COSINE_THRESHOLD,
    GRADUATION_COUNT,
    HIGH_STAKES_TYPES,
    FuzzyDedupeConfig,
    FuzzyDedupeResult,
    FuzzyReviewAlreadyDecidedError,
    FuzzyReviewNotFoundError,
    FuzzyReviewSignalMissingError,
    apply_fuzzy_review,
    is_high_stakes_type,
    run_fuzzy_dedupe,
)
from .models import (
    SIGNAL_STATUS_MERGED,
    SIGNAL_STATUS_NEW,
    SIGNAL_STATUS_PENDING_REVIEW,
    Signal,
)
from .models_fuzzy_review import (
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    SignalFuzzyReview,
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
from .workspace_score_model import (
    FEED_VISIBLE_STATUSES,
    MATCHED_VIA_ICP,
    SCORE_STATUSES,
    WorkspaceScore,
)
from .workspace_scoring import (
    DEFAULT_SCORING_CONFIG,
    IcpCriteria,
    KeywordExcludedError,
    ScoringConfig,
    SignalDimensions,
    WorkspaceScoreResult,
    score_signal_against_icp,
    signal_matches_icp,
)

log = structlog.get_logger(__name__)

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
    *,
    fuzzy_config: FuzzyDedupeConfig = DEFAULT_FUZZY_CONFIG,
) -> Signal:
    """Validate + promote a candidate into a global ``signals_signal`` row (E4/E10).

    The funnel's "store" step (doc 19 §1, §6.1). Three parts:

    1. **Strict gate** (doc 19 §6.1, hard gate): the candidate's ``signal_type`` +
       ``fields`` are validated against the matching per-type schema via
       :func:`parse_signal_payload`. An invalid candidate raises
       :class:`SignalValidationError` — the extraction task turns that into the
       retry → dead-letter path with the surfaced error. **No partial signal is
       written on a validation failure.**
    2. **Exact-key windowed dedupe** (doc 19 §7.1-§7.3; E5): compute the canonical
       per-type dedupe hash from ``entity_id + signal_type + normalized_key_fields``,
       look for an existing signal with that hash within the type-specific window;
       on a hit **merge** the new corroborating document(s) + higher confidence into
       the surviving signal (doc 19 §7.3, no source doc lost); else INSERT a new row.
    3. **Embedding-based fuzzy dedupe** (doc 19 §7.4; E10) — only for high-stakes
       types (``rfp_posted``, ``contract_expiring``) when exact-match found nothing:
       embed the candidate's title+summary, ANN-query for ≥ 0.92 cosine similarity
       within entity + signal_type + window. First 100 matches per type → human
       review queue; after graduation → auto-merge (see :func:`run_fuzzy_dedupe`).

    The caller owns the transaction (this flushes, not commits).

    The confidence band (doc 19 §6.2-§6.3; E6) is supplied on ``candidate.band``
    when the extraction score stage computed it, or derived from
    ``candidate.confidence`` against the default thresholds otherwise; it sets the
    row's ``status``/``is_degraded``/``review_required`` flags. A ``rejected``-band
    candidate must not reach here — the pipeline drops it before store (doc 19 §6.3).
    """
    payload = parse_signal_payload(candidate.signal_type, candidate.fields)
    return await store_signal(session, payload, candidate, fuzzy_config=fuzzy_config)


async def store_signal(
    session: AsyncSession,
    payload: SignalPayload,
    candidate: CandidateInput,
    *,
    fuzzy_config: FuzzyDedupeConfig = DEFAULT_FUZZY_CONFIG,
) -> Signal:
    """Store a validated payload into ``signals_signal`` with windowed dedupe (doc 19 §7).

    Separated from :func:`promote_candidate_to_signal` so a caller that already
    holds a validated :class:`SignalPayload` (e.g. a backfill or a test) can store
    it directly. Computes the canonical per-type dedupe hash, runs the type-windowed
    duplicate lookup (doc 19 §7.2), and either merges into the surviving signal
    (doc 19 §7.3) or inserts a new one. For high-stakes types with no exact match,
    runs embedding-based fuzzy dedupe (doc 19 §7.4; E10). The caller commits.
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

    # No exact duplicate within the window — insert a new signal row.
    # For high-stakes types (doc 19 §7.4; E10) we then run the embedding-based
    # fuzzy fallback against the newly-inserted row: insert first so the row
    # has an id (needed by the review table FK-lookalike) and the ANN query
    # can exclude it from its own results.
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
    await session.flush()  # assign id before fuzzy path needs it

    # E10: embedding-based fuzzy dedupe for high-stakes types (doc 19 §7.4).
    # Only runs when exact-match found nothing (we are here) and the type qualifies.
    if is_high_stakes_type(payload.signal_type, config=fuzzy_config):
        fuzzy_result = await run_fuzzy_dedupe(
            session,
            candidate_signal=row,
            signal_type=payload.signal_type,
            new_doc_ids=raw_doc_ids,
            new_confidence=candidate.confidence,
            config=fuzzy_config,
            now=now,
        )
        if fuzzy_result.auto_merged and fuzzy_result.matched_signal_id is not None:
            # Auto-merge folded the new evidence into the surviving signal; soft-delete
            # the candidate row so it no longer surfaces in list_signals / feed queries.
            # The row is kept for the audit trail (its raw_document_ids were merged into
            # the surviving signal by run_fuzzy_dedupe → merge_signal above).
            row.status = SIGNAL_STATUS_MERGED
            row.merged_into = fuzzy_result.matched_signal_id
            await session.flush()
            surviving = await session.get(Signal, fuzzy_result.matched_signal_id)
            if surviving is not None:
                return surviving

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
# Fuzzy-review read / decision seam (E10 — doc 19 §7.4)
# ---------------------------------------------------------------------------


async def get_fuzzy_review(
    session: AsyncSession,
    review_id: uuid.UUID,
) -> SignalFuzzyReview | None:
    """Fetch one fuzzy-review row by id (E10 review endpoint read seam)."""
    return await session.get(SignalFuzzyReview, review_id)


async def list_fuzzy_reviews(
    session: AsyncSession,
    *,
    signal_type: str | None = None,
    status: str | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[list[SignalFuzzyReview], str | None]:
    """Cursor-paginated list of fuzzy-review rows (E10; doc 19 §7.4).

    Ordered ``created_at DESC, id DESC`` (newest first). Filters by ``signal_type``
    and/or ``status`` (pending / approved / rejected). Returns ``(items, next_cursor)``
    where ``next_cursor`` is a base64-encoded id or ``None`` if no further pages.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    stmt = select(SignalFuzzyReview)
    if signal_type is not None:
        stmt = stmt.where(SignalFuzzyReview.signal_type == signal_type)
    if status is not None:
        stmt = stmt.where(SignalFuzzyReview.status == status)
    if cursor is not None:
        cursor_id = decode_cursor(cursor)
        cursor_row = await session.get(SignalFuzzyReview, cursor_id)
        if cursor_row is None:
            raise ValueError("invalid cursor")
        stmt = stmt.where(
            (SignalFuzzyReview.created_at < cursor_row.created_at)
            | (
                (SignalFuzzyReview.created_at == cursor_row.created_at)
                & (SignalFuzzyReview.id < cursor_id)
            )
        )
    stmt = stmt.order_by(SignalFuzzyReview.created_at.desc(), SignalFuzzyReview.id.desc()).limit(
        limit + 1
    )
    rows = list((await session.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].id) if has_more and items else None
    return items, next_cursor


async def decide_fuzzy_review(
    session: AsyncSession,
    review_id: uuid.UUID,
    *,
    approved: bool,
    reviewer_note: str | None = None,
) -> SignalFuzzyReview:
    """Approve or reject a pending fuzzy-review row (E10; doc 19 §7.4).

    Public seam so the routes module + any cross-module caller never imports
    :mod:`signals.fuzzy_dedupe` directly (doc 06 §3). Delegates to
    :func:`~signals.fuzzy_dedupe.apply_fuzzy_review`. Raises
    :class:`FuzzyReviewNotFoundError` (404) or
    :class:`FuzzyReviewAlreadyDecidedError` (409) on precondition failures.
    """
    return await apply_fuzzy_review(
        session,
        review_id,
        approved=approved,
        reviewer_note=reviewer_note,
    )


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

    stmt = select(Signal).where(Signal.status != SIGNAL_STATUS_MERGED)
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


# ---------------------------------------------------------------------------
# Per-workspace matcher + scorer (F3, doc 14 §6)
# ---------------------------------------------------------------------------
#
# The cheap pre-filter (doc 14 §6.1) is the SQL candidate query below; the full
# score (doc 14 §6.2) blends the components in ``signals.workspace_scoring`` and
# the result is sparse-upserted into ``signals_workspace_score`` when it clears the
# ICP threshold (doc 14 §5.2, §6.2). The pure matcher predicate + blend math live in
# ``workspace_scoring`` (no DB); this layer does the joins, the fan-out, and the
# upsert. Cross-module callers reach all of this through ``signals.services``.


def _icp_criteria(icp: IcpDefinition) -> IcpCriteria:
    """Snapshot an ICP row into the matcher/scorer's pure :class:`IcpCriteria`.

    Decouples the scorer from the ``icp`` ORM so ``workspace_scoring`` stays pure
    (no DB import). Threshold is on the 0..100 scale shared with the score (doc 07
    §icp, doc 14 §5.2).
    """
    return IcpCriteria(
        signal_types=tuple(icp.signal_types),
        countries=tuple(icp.countries),
        states=tuple(icp.states),
        entity_kinds=tuple(icp.entity_kinds),
        min_size=icp.min_size,
        max_size=icp.max_size,
        signal_weights={k: float(v) for k, v in icp.signal_weights.items()},
        keywords_required=tuple(icp.keywords_required),
        keywords_excluded=tuple(icp.keywords_excluded),
        threshold=float(icp.threshold),
    )


def _signal_dimensions(signal: Signal, entity: Entity | None) -> SignalDimensions:
    """Resolve a signal's matchable dimensions from the signal + its entity (§6.1).

    Country / state / entity-kind / size come from the resolved ``entities_entity``
    (doc 14 §6.1); an unresolved signal (``entity_id`` NULL, doc 19 §4.3) carries no
    geo/kind/size, so those stay ``None`` and only match an ICP that does not
    restrict them.
    """
    size: int | None = None
    country: str | None = None
    state: str | None = None
    entity_kind: str | None = None
    if entity is not None:
        country = entity.country
        state = entity.state
        entity_kind = entity.type
        # Entity size = enrollment (school orgs) or population (general government),
        # whichever the directory has — the band the ICP compares against (§6.1).
        size = entity.enrollment if entity.enrollment is not None else entity.population
    return SignalDimensions(
        signal_type=signal.signal_type,
        country=country,
        state=state,
        entity_kind=entity_kind,
        size=size,
    )


def _signal_text_blob(signal: Signal) -> str:
    """The text surface keyword scoring runs over (doc 14 §6.2).

    Reuses the embedding text builder (title + summary + a few high-signal details
    fields) so keyword matching and semantic matching share one surface.
    """
    return embedding_text_for_signal(signal)


def candidate_icp_filter(dims: SignalDimensions) -> Any:
    """The SQL pre-filter predicate matching ``signal_matches_icp`` (doc 14 §6.1).

    Encodes "could this signal match this ICP?" as a WHERE clause over the
    GIN-indexed ICP dimension columns: empty array == all values, else ``@>`` set
    membership; the size band is a numeric range with NULL bounds meaning
    open-ended (doc 14 §6.1). An unknown signal dimension (``None``) can only
    satisfy an *unrestricted* ICP dimension — the same NULL-excludes rule the pure
    :func:`~.workspace_scoring.signal_matches_icp` applies — so a restricting array
    is required to be empty when the signal value is unknown.
    """
    clauses: list[Any] = [IcpDefinition.is_active.is_(True)]

    def _array_clause(column: Any, value: str | None) -> Any:
        # Empty TEXT[] == "all values" (doc 14 §6.1). ``func.cardinality`` is the
        # portable "array is empty" test (avoids the SQLAlchemy ``== []`` warning).
        empty = func.cardinality(column) == 0
        if value is None:
            # Unknown dimension: only an unrestricted (empty) ICP array matches.
            return empty
        return or_(empty, column.contains([value]))

    clauses.append(_array_clause(IcpDefinition.signal_types, dims.signal_type))
    clauses.append(_array_clause(IcpDefinition.countries, dims.country))
    clauses.append(_array_clause(IcpDefinition.states, dims.state))
    clauses.append(_array_clause(IcpDefinition.entity_kinds, dims.entity_kind))

    if dims.size is None:
        # Unknown size only satisfies an ICP with no size band (both bounds NULL).
        clauses.append(IcpDefinition.min_size.is_(None))
        clauses.append(IcpDefinition.max_size.is_(None))
    else:
        clauses.append(or_(IcpDefinition.min_size.is_(None), IcpDefinition.min_size <= dims.size))
        clauses.append(or_(IcpDefinition.max_size.is_(None), IcpDefinition.max_size >= dims.size))
    return and_(*clauses)


async def _load_signal_with_entity(
    session: AsyncSession, signal_id: uuid.UUID
) -> tuple[Signal, Entity | None] | None:
    """Load a signal + its (optional) resolved entity for scoring, or ``None``."""
    signal = await session.get(Signal, signal_id)
    if signal is None:
        return None
    entity: Entity | None = None
    if signal.entity_id is not None:
        entity = await session.get(Entity, signal.entity_id)
    return signal, entity


async def _upsert_score(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    signal_id: uuid.UUID,
    result: WorkspaceScoreResult,
) -> None:
    """Sparse upsert one ``signals_workspace_score`` row (doc 14 §5.2, §7.3).

    ``INSERT ... ON CONFLICT (workspace_id, signal_id) DO UPDATE`` so re-scoring (a
    new signal arriving, an F6 backfill, an F5 re-weight) is idempotent — the score /
    breakdown / matched flags are refreshed without resetting the user-owned
    ``status`` (doc 14 §7.3: existing-still-valid rows are not reset). The caller
    owns the transaction.
    """
    dims = result.matched
    values = {
        # UUID v7 (time-ordered) so the feed cursor's id tiebreaker stays stable
        # and index locality holds (matches the model's ``default=uuid7``); a fresh
        # id is only used on INSERT — the ON CONFLICT path keeps the existing row.
        "id": uuid7(),
        "workspace_id": workspace_id,
        "signal_id": signal_id,
        "score": result.score,
        "score_breakdown": result.breakdown,
        "matched_via": MATCHED_VIA_ICP,
        "matched_signal_type": dims.signal_type,
        "matched_country": dims.country,
        "matched_state": dims.state,
        "matched_entity_kind": dims.entity_kind,
        "matched_size_band": dims.size_band,
        "matched_keywords": list(result.matched_keywords),
    }
    stmt = pg_insert(WorkspaceScore).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_sws_workspace_signal",
        set_={
            "score": stmt.excluded.score,
            "score_breakdown": stmt.excluded.score_breakdown,
            "matched_signal_type": stmt.excluded.matched_signal_type,
            "matched_country": stmt.excluded.matched_country,
            "matched_state": stmt.excluded.matched_state,
            "matched_entity_kind": stmt.excluded.matched_entity_kind,
            "matched_size_band": stmt.excluded.matched_size_band,
            "matched_keywords": stmt.excluded.matched_keywords,
            "updated_at": datetime.now(UTC),
        },
    )
    await session.execute(stmt)


async def _delete_score(
    session: AsyncSession, *, workspace_id: uuid.UUID, signal_id: uuid.UUID
) -> None:
    """Remove a stale score row (a re-score that now falls below threshold / excluded).

    When a re-score (an ICP edit via F6, an F5 re-weight) drops a previously-matched
    signal below the threshold or trips an excluded keyword, the existing row must go
    so the feed no longer shows it (doc 14 §7.3 idempotency). A no-op when no row
    exists. The caller owns the transaction.
    """
    await session.execute(
        delete(WorkspaceScore).where(
            WorkspaceScore.workspace_id == workspace_id,
            WorkspaceScore.signal_id == signal_id,
        )
    )


async def score_signal_for_workspace(
    session: AsyncSession,
    *,
    signal_id: uuid.UUID,
    workspace_id: uuid.UUID,
    icp: IcpDefinition | None = None,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
    icp_vector: Sequence[float] | None = None,
    now: datetime | None = None,
) -> WorkspaceScoreResult | None:
    """Score one signal against one workspace's active ICP (doc 14 §6.2).

    The single-pair scorer F4/F6 build on. Loads the signal + its entity, snapshots
    the workspace's active ICP (looked up via ``icp.services.get_active_icp`` unless
    one is passed in), pre-filters (doc 14 §6.1), full-scores (doc 14 §6.2), and:

    - **at/above threshold** → sparse-upsert a ``signals_workspace_score`` row and
      return the :class:`~.workspace_scoring.WorkspaceScoreResult`;
    - **below threshold / pre-filter miss / excluded keyword** → remove any stale row
      and return ``None`` (the workspace simply does not see the signal, doc 14 §5.2).

    The caller owns the transaction (this flushes via the session, not commits). The
    optional ``icp_vector`` is the ICP-keyword embedding (I1) for the semantic
    component; when ``None`` the score blends the structured + keyword signal alone
    (the semantic weight renormalises away, doc 14 §6.2).
    """
    # Lazy import to avoid a module-load cycle (icp.services has no signals import,
    # but keep the dependency one-directional + explicit at the call site).
    from civicsignals_api.modules.icp import services as icp_services

    loaded = await _load_signal_with_entity(session, signal_id)
    if loaded is None:
        return None
    signal, entity = loaded

    if icp is None:
        icp = await icp_services.get_active_icp(session, workspace_id=workspace_id)
    if icp is None:
        # No active ICP → the workspace has no feed lens; nothing to score against.
        await _delete_score(session, workspace_id=workspace_id, signal_id=signal_id)
        return None

    criteria = _icp_criteria(icp)
    dims = _signal_dimensions(signal, entity)

    if not signal_matches_icp(dims, criteria):
        await _delete_score(session, workspace_id=workspace_id, signal_id=signal_id)
        return None

    try:
        result = score_signal_against_icp(
            dims,
            criteria,
            text_blob=_signal_text_blob(signal),
            confidence=signal.confidence,
            observed_at=signal.observed_at,
            signal_vector=signal.vector_embedding,
            icp_vector=icp_vector,
            config=config,
            now=now,
        )
    except KeywordExcludedError:
        # Excluded keyword / no required-keyword hit → hard drop (doc 14 §3.1).
        await _delete_score(session, workspace_id=workspace_id, signal_id=signal_id)
        return None

    if not result.passes_threshold:
        await _delete_score(session, workspace_id=workspace_id, signal_id=signal_id)
        return None

    await _upsert_score(session, workspace_id=workspace_id, signal_id=signal_id, result=result)
    return result


async def score_signal_for_all_workspaces(
    session: AsyncSession,
    *,
    signal_id: uuid.UUID,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
    now: datetime | None = None,
) -> int:
    """Fan a new signal out to every workspace whose ICP pre-filter matches (§6).

    The live matcher path triggered on ``signal.created`` (doc 14 §4.1): resolve the
    signal's dimensions, run the **cheap pre-filter** (doc 14 §6.1) as one indexed SQL
    query that returns only candidate active ICPs (the matcher is workspace-blind to
    non-overlapping ICPs — doc 14 §6.4), then full-score each candidate and sparse-
    upsert the rows that clear threshold (doc 14 §6.2). Returns the number of score
    rows written. The caller owns the transaction.

    # TODO F6: this is the new-signal path. The ICP-change *backfill* (doc 14 §7) runs
    # the same pre-filter over a historical signal window for one workspace — the
    # symmetric direction — and upserts via the same ``_upsert_score`` seam.
    """
    loaded = await _load_signal_with_entity(session, signal_id)
    if loaded is None:
        return 0
    signal, entity = loaded
    dims = _signal_dimensions(signal, entity)

    # Cheap pre-filter (doc 14 §6.1): one indexed query → only candidate ICPs.
    candidates = list(
        (await session.execute(select(IcpDefinition).where(candidate_icp_filter(dims))))
        .scalars()
        .all()
    )

    written = 0
    for icp in candidates:
        result = await score_signal_for_workspace(
            session,
            signal_id=signal_id,
            workspace_id=icp.workspace_id,
            icp=icp,
            config=config,
            now=now,
        )
        if result is not None:
            written += 1
    log.info(
        "signals.workspace_score.fanout",
        signal_id=str(signal_id),
        candidates=len(candidates),
        written=written,
    )
    return written


async def score_workspace_candidates(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    signal_ids: Sequence[uuid.UUID],
    icp: IcpDefinition | None = None,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
    icp_vector: Sequence[float] | None = None,
    now: datetime | None = None,
) -> int:
    """Score a batch of candidate signals for one workspace (doc 14 §6.2, §7).

    The workspace-centric direction the F6 backfill drives: given a set of candidate
    signal ids (the backfill pre-filters the historical window into these), score
    each against the workspace's active ICP and sparse-upsert the matches. Loads the
    active ICP once (unless passed in) so a long backfill does not re-query it per
    signal. Returns the number of score rows written. Idempotent: re-running with the
    same signal ids updates (via ON CONFLICT) existing score rows rather than
    inserting duplicates (doc 14 §7.3). The caller owns the transaction.
    """
    from civicsignals_api.modules.icp import services as icp_services

    if icp is None:
        icp = await icp_services.get_active_icp(session, workspace_id=workspace_id)
    if icp is None:
        return 0

    written = 0
    for signal_id in signal_ids:
        result = await score_signal_for_workspace(
            session,
            signal_id=signal_id,
            workspace_id=workspace_id,
            icp=icp,
            config=config,
            icp_vector=icp_vector,
            now=now,
        )
        if result is not None:
            written += 1
    return written


# ---------------------------------------------------------------------------
# Backfill candidate pre-filter (F6, doc 14 §7.1)
# ---------------------------------------------------------------------------

# How many candidates to score synchronously in the route path so the feed has
# immediate results (doc 14 §7.2). The remaining candidates are handed to
# ``signals.rescore_workspace`` (Celery, score queue) in bounded batches.
BACKFILL_SYNC_LIMIT: int = 1_000

# Batch size for the async (Celery) backfill pass (doc 14 §7.2). Each batch is a
# separate DB transaction so a failure is bounded and the backfill can resume from
# where the cursor left off.
BACKFILL_BATCH_SIZE: int = 200

# How far back the backfill looks (doc 14 §7.1). Signals older than this are
# unlikely to be acted on even if matched; the value keeps the backfill bounded.
BACKFILL_LOOKBACK_DAYS: int = 180


async def candidate_signal_ids_for_icp(
    session: AsyncSession,
    icp: IcpDefinition,
    *,
    offset: int = 0,
    limit: int | None = None,
) -> list[uuid.UUID]:
    """Fetch candidate signal ids that *could* match ``icp`` (the pre-filter, §6.1).

    Runs the same SQL predicate as :func:`candidate_icp_filter` but in the
    *workspace-centric* direction: given a fixed ICP, return the global signal ids
    whose dimensions overlap it, ordered ``observed_at DESC`` (most recent first) so
    the sync batch surfaces the freshest signals first.

    The result is a flat list of ids; scoring (the expensive part) is deferred to
    :func:`score_workspace_candidates`. ``offset`` + ``limit`` let the Celery task
    page through the remainder after the sync batch claimed the first
    :data:`BACKFILL_SYNC_LIMIT` rows.
    """
    from datetime import timedelta

    from .models import SIGNAL_STATUS_MERGED

    # Mirror the signal_matches_icp predicate as a SQL WHERE clause over the
    # signals table.  We join to entities_entity when we need geo/kind/size dims;
    # here we only need the signal_type dimension (the cheapest pre-filter) plus
    # the entity join for country/state/size — same logic as the ICP candidate query
    # in score_signal_for_all_workspaces.  To stay simple (and avoid a fat join),
    # we load the signal ids and let score_signal_for_workspace do the full check.
    #
    # The cheap pre-filter here: restrict by signal_type if the ICP narrows it, and
    # cutoff by lookback window.  The remaining dimensions are cheap to check in
    # Python (via signal_matches_icp) since we only load the id + type here.
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=BACKFILL_LOOKBACK_DAYS)

    stmt = (
        select(Signal.id)
        .where(Signal.status != SIGNAL_STATUS_MERGED)
        .where(Signal.observed_at >= cutoff)
    )

    if icp.signal_types:
        stmt = stmt.where(Signal.signal_type.in_(icp.signal_types))

    stmt = stmt.order_by(Signal.observed_at.desc(), Signal.id.desc())
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = list((await session.execute(stmt)).scalars().all())
    return rows


# ---------------------------------------------------------------------------
# The G1 feed read seam (doc 14 §5.3)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WorkspaceFeedItem:
    """One scored signal in a workspace's feed (the join of score + signal, §5.3).

    Carries the ``signals_workspace_score`` row's score / breakdown / status / matched
    flags alongside the global :class:`SignalRead` so G1 renders the feed item + its
    "Why this signal?" panel from one read.
    """

    score_id: uuid.UUID
    signal: SignalRead
    score: float
    status: str
    score_breakdown: dict[str, Any]
    matched_keywords: list[str]
    created_at: datetime


@dataclass(slots=True)
class WorkspaceFeedPage:
    """A cursor-paginated page of feed items (doc 06 §5)."""

    items: list[WorkspaceFeedItem]
    next_cursor: str | None


# ---------------------------------------------------------------------------
# The G2 signal-detail read seam (doc 07 §2, doc 14 §5.3)
# ---------------------------------------------------------------------------
#
# The detail page (G2) shows one signal in full: the global signal core, the
# *calling workspace's* score / breakdown / status (when the signal scored into
# this workspace's feed — the row is workspace-scoped, never another tenant's),
# the source documents that corroborate it, the suggested contacts at the
# signal's entity, and related signals about the same entity. The cross-module
# data (source docs, contacts, entity name) is fetched through the owning
# module's ``services.py`` only — no module reaches into another's models
# (doc 06 §3) — so this aggregator imports ``ingestion.services`` /
# ``contacts.services`` / ``entities.services`` lazily at call time (the same
# one-directional, explicit-at-call-site pattern as the F3 scorer above).

# How many related signals + suggested contacts the detail view surfaces. Bounded
# so the detail read stays a fixed handful of cheap queries (the full lists live on
# the entity profile / contacts pages).
RELATED_SIGNALS_LIMIT: int = 10
SUGGESTED_CONTACTS_LIMIT: int = 10


@dataclass(slots=True)
class SignalSourceDocument:
    """One corroborating source document for a signal (G2; doc 19 §7.3).

    A lightweight provenance projection of an ``ingestion_raw_document`` row
    (resolved via ``ingestion.services.get_raw_document``) — the originating URL,
    recipe, fetch time, and content type so the detail page can list "where this
    came from" and deep-link to the source. ``missing`` flags a referenced id that
    no longer resolves (a doc pruned after the signal was stored) so the UI can
    show a tombstone rather than silently dropping it.
    """

    raw_document_id: uuid.UUID
    recipe_id: str | None
    source_url: str | None
    fetched_at: datetime | None
    content_type: str | None
    missing: bool = False


@dataclass(slots=True)
class SuggestedContact:
    """One suggested contact at the signal's entity (G2; doc 07 §2 contacts).

    A projection of a global ``contacts_contact`` row (via
    ``contacts.services.list_contacts_for_entity``). Contacts are global per entity
    (doc 07 §3), so this is the same directory the entity profile shows — surfaced
    here so a user acting on a signal can reach the right person without leaving the
    detail page. ``None`` entity → no suggestions (an unresolved signal, doc 19 §4.3).
    """

    contact_id: uuid.UUID
    name: str
    title: str | None
    department: str | None
    canonical_email: str | None
    status: str
    verified: bool


@dataclass(slots=True)
class RelatedSignal:
    """One related signal about the same entity (G2; doc 14 §5.3).

    Other (non-merged) signals for the signal's resolved entity, newest-first,
    excluding the signal itself. Carries the global signal core only — the related
    signal's per-workspace score is not joined here (the user can open it to see
    that). ``None`` entity → no related signals (cannot relate by entity).
    """

    signal: SignalRead


@dataclass(slots=True)
class WorkspaceSignalDetail:
    """The full G2 signal-detail view for one (workspace, signal) pair.

    Aggregates the global signal, the calling workspace's score row (when present —
    a signal can be opened by id even if it did not score into this workspace's
    feed, in which case ``score``/``status``/``score_breakdown`` are ``None``), the
    resolved entity name, the corroborating source documents, the suggested contacts
    at the entity, and the related signals about the same entity. The
    ``extracted_fields`` is the validated per-type payload (``details_jsonb``) lifted
    out for the "Extracted fields" + inspect panel; it is the same map as
    ``signal.details`` (surfaced explicitly so the UI need not reach into ``signal``).
    """

    signal: SignalRead
    entity_id: uuid.UUID | None
    entity_name: str | None
    score: float | None
    status: str | None
    score_breakdown: dict[str, Any] | None
    matched_keywords: list[str]
    extracted_fields: dict[str, Any]
    source_documents: list[SignalSourceDocument]
    suggested_contacts: list[SuggestedContact]
    related_signals: list[RelatedSignal]


async def _related_signals_for_entity(
    session: AsyncSession,
    *,
    entity_id: uuid.UUID,
    exclude_signal_id: uuid.UUID,
    limit: int,
) -> list[RelatedSignal]:
    """Load up to ``limit`` non-merged signals for an entity, newest-first (G2).

    Excludes the signal being viewed and any soft-deleted (``merged``) rows so the
    detail page's "Related signals" section mirrors what ``list_signals`` would show
    for the same entity (doc 19 §7.4 merged rows stay hidden).
    """
    stmt = (
        select(Signal)
        .where(Signal.entity_id == entity_id)
        .where(Signal.id != exclude_signal_id)
        .where(Signal.status != SIGNAL_STATUS_MERGED)
        .order_by(Signal.observed_at.desc(), Signal.id.desc())
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return [RelatedSignal(signal=SignalRead.model_validate(r)) for r in rows]


async def _source_documents_for_signal(
    session: AsyncSession,
    raw_document_ids: Sequence[uuid.UUID],
) -> list[SignalSourceDocument]:
    """Resolve a signal's ``raw_document_ids`` to source-document projections (G2).

    Reads each referenced ``ingestion_raw_document`` through
    ``ingestion.services.get_raw_document`` (the owning module's seam, doc 06 §3); a
    referenced id that no longer resolves is kept as a ``missing`` tombstone so the
    provenance list stays complete and explainable.
    """
    from civicsignals_api.modules.ingestion import services as ingestion_services

    docs: list[SignalSourceDocument] = []
    for doc_id in raw_document_ids:
        stored = await ingestion_services.get_raw_document(session, doc_id)
        if stored is None:
            docs.append(SignalSourceDocument(raw_document_id=doc_id, recipe_id=None,
                                             source_url=None, fetched_at=None,
                                             content_type=None, missing=True))
            continue
        docs.append(
            SignalSourceDocument(
                raw_document_id=stored.id,
                recipe_id=stored.recipe_id,
                source_url=stored.source_url,
                fetched_at=stored.fetched_at,
                content_type=stored.content_type,
            )
        )
    return docs


async def get_signal_detail(
    session: AsyncSession,
    *,
    signal_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> WorkspaceSignalDetail | None:
    """The full G2 detail view for one signal in one workspace's context.

    Returns ``None`` only when the global signal does not exist (or is a soft-deleted
    ``merged`` row — those are not directly viewable). A signal that exists but did
    **not** score into the calling workspace's feed is still returned, with the
    per-workspace fields (``score``/``status``/``score_breakdown``) left ``None`` —
    the signal corpus is global (doc 07 §3), so the detail page can be reached by id
    (e.g. a shared link) even from a workspace whose ICP did not match it.

    The per-workspace score row is read **scoped to the calling workspace** via the
    ``(workspace_id, signal_id)`` unique key — workspace A can never read workspace
    B's score for the same global signal (doc 14 §5.3). Source documents, suggested
    contacts, and the resolved entity name come through the owning modules'
    ``services.py`` (doc 06 §3). Related signals are other non-merged signals for the
    same entity. The caller does not commit (this only reads).

    TODO F4: the "Why this signal?" panel renders human-readable bullets from
    ``score_breakdown`` — the structured breakdown is already returned here.
    TODO G4: status transitions (dismiss/pin/push) will PATCH the score row this
    view reads ``status`` from.
    """
    signal = await session.get(Signal, signal_id)
    if signal is None or signal.status == SIGNAL_STATUS_MERGED:
        return None

    # The calling workspace's score row for this signal, if any (scoped to the
    # workspace — never another tenant's). A signal can be viewed without a score
    # row (it simply did not match this workspace's ICP); the per-workspace fields
    # stay None in that case.
    score_row = (
        await session.execute(
            select(WorkspaceScore)
            .where(WorkspaceScore.workspace_id == workspace_id)
            .where(WorkspaceScore.signal_id == signal_id)
        )
    ).scalar_one_or_none()

    entity_id = signal.entity_id
    entity_name: str | None = signal.entity_name_raw
    suggested_contacts: list[SuggestedContact] = []
    related_signals: list[RelatedSignal] = []
    if entity_id is not None:
        from civicsignals_api.modules.contacts import services as contact_services
        from civicsignals_api.modules.entities import services as entity_services

        entity = await entity_services.get_entity(session, entity_id)
        if entity is not None:
            entity_name = entity.name
        contact_page = await contact_services.list_contacts_for_entity(
            session, entity_id, limit=SUGGESTED_CONTACTS_LIMIT
        )
        suggested_contacts = [
            SuggestedContact(
                contact_id=c.id,
                name=c.name,
                title=c.title,
                department=c.department,
                canonical_email=c.canonical_email,
                status=c.status,
                verified=c.verified,
            )
            for c in contact_page.items
        ]
        related_signals = await _related_signals_for_entity(
            session,
            entity_id=entity_id,
            exclude_signal_id=signal_id,
            limit=RELATED_SIGNALS_LIMIT,
        )

    source_documents = await _source_documents_for_signal(
        session, [uuid.UUID(d) for d in signal.raw_document_ids]
    )

    return WorkspaceSignalDetail(
        signal=SignalRead.model_validate(signal),
        entity_id=entity_id,
        entity_name=entity_name,
        score=float(score_row.score) if score_row is not None else None,
        status=score_row.status if score_row is not None else None,
        score_breakdown=score_row.score_breakdown if score_row is not None else None,
        matched_keywords=list(score_row.matched_keywords) if score_row is not None else [],
        extracted_fields=dict(signal.details),
        source_documents=source_documents,
        suggested_contacts=suggested_contacts,
        related_signals=related_signals,
    )


async def list_workspace_signals(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    signal_type: str | None = None,
    statuses: Sequence[str] | None = None,
    min_score: float | None = None,
    published_at_gte: datetime | None = None,
    published_at_lt: datetime | None = None,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> WorkspaceFeedPage:
    """The workspace feed: scored signals ranked highest-first (G1, doc 14 §5.3).

    Reads ``signals_workspace_score`` for the workspace (workspace-scoped — never
    another tenant's rows), joined to the global ``signals_signal`` row, ordered by
    ``score DESC, created_at DESC, id DESC`` — the hot composite index (doc 14 §5.3).
    Filters to the feed-visible statuses (new/reviewed/pinned) by default; pass
    ``statuses`` to override (e.g. include ``dismissed`` for a "show hidden" view).
    Optional ``signal_type`` narrows to one type; ``min_score`` (0-100) filters below
    the given threshold; ``published_at_gte``/``published_at_lt`` bound the signal's
    ``occurred_at`` date (doc 08 §1.6 date-range conventions).

    Cursor pagination is keyset on the score row id (UUID v7, time-ordered) so the
    opaque cursor is stable; we fetch ``limit + 1`` to decide ``next_cursor``. The id
    tiebreaker keeps the ordering total even when many rows share a score.

    This is what G1's feed UI calls; it is **the** per-workspace ranked surface F3
    exists to produce (doc 14 §1).
    """
    limit = max(1, min(limit, MAX_LIMIT))
    if statuses is None:
        # No filter requested → the default feed-visible statuses (doc 14 §5.3).
        status_filter: Sequence[str] = FEED_VISIBLE_STATUSES
    else:
        # An explicit filter — drop unknown values. If the caller asked only for
        # invalid statuses, the result is an *empty* feed (not "all statuses"): the
        # request named no valid bucket, so nothing matches.
        status_filter = [s for s in statuses if s in SCORE_STATUSES]
        if not status_filter:
            return WorkspaceFeedPage(items=[], next_cursor=None)

    stmt = (
        select(WorkspaceScore, Signal)
        .join(Signal, Signal.id == WorkspaceScore.signal_id)
        .where(WorkspaceScore.workspace_id == workspace_id)
        .where(WorkspaceScore.status.in_(status_filter))
    )
    if signal_type is not None:
        stmt = stmt.where(Signal.signal_type == signal_type)
    if min_score is not None:
        # ``score`` is Numeric(5, 2) on the 0..100 scale (doc 14 §5.3).
        stmt = stmt.where(WorkspaceScore.score >= min_score)
    if published_at_gte is not None:
        stmt = stmt.where(Signal.occurred_at >= published_at_gte)
    if published_at_lt is not None:
        stmt = stmt.where(Signal.occurred_at < published_at_lt)

    if cursor is not None:
        cursor_id = decode_cursor(cursor)
        cursor_row = await session.get(WorkspaceScore, cursor_id)
        if cursor_row is None or cursor_row.workspace_id != workspace_id:
            raise ValueError("invalid cursor")
        # Keyset on (score DESC, created_at DESC, id DESC): strictly "after" the
        # cursor row in the feed order.
        stmt = stmt.where(
            or_(
                WorkspaceScore.score < cursor_row.score,
                and_(
                    WorkspaceScore.score == cursor_row.score,
                    WorkspaceScore.created_at < cursor_row.created_at,
                ),
                and_(
                    WorkspaceScore.score == cursor_row.score,
                    WorkspaceScore.created_at == cursor_row.created_at,
                    WorkspaceScore.id < cursor_id,
                ),
            )
        )

    stmt = stmt.order_by(
        WorkspaceScore.score.desc(),
        WorkspaceScore.created_at.desc(),
        WorkspaceScore.id.desc(),
    ).limit(limit + 1)

    rows = list((await session.execute(stmt)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [
        WorkspaceFeedItem(
            score_id=score.id,
            signal=SignalRead.model_validate(signal),
            score=float(score.score),
            status=score.status,
            score_breakdown=score.score_breakdown,
            matched_keywords=list(score.matched_keywords),
            created_at=score.created_at,
        )
        for score, signal in rows
    ]
    next_cursor = encode_cursor(items[-1].score_id) if has_more and items else None
    return WorkspaceFeedPage(items=items, next_cursor=next_cursor)


__all__ = [
    "BACKFILL_BATCH_SIZE",
    "BACKFILL_LOOKBACK_DAYS",
    "BACKFILL_SYNC_LIMIT",
    "DEDUPE_WINDOWS",
    "DEFAULT_CONFIG",
    "DEFAULT_DEDUPE_WINDOW",
    "DEFAULT_FUZZY_CONFIG",
    "DEFAULT_LIMIT",
    "DEFAULT_SCORING_CONFIG",
    "FUZZY_COSINE_THRESHOLD",
    "GRADUATION_COUNT",
    "HIGH_STAKES_TYPES",
    "MAX_LIMIT",
    "PAYLOAD_BY_TYPE",
    "RELATED_SIGNALS_LIMIT",
    "REVIEW_STATUS_APPROVED",
    "REVIEW_STATUS_PENDING",
    "REVIEW_STATUS_REJECTED",
    "SIGNAL_STATUS_MERGED",
    "SUGGESTED_CONTACTS_LIMIT",
    "BandThresholds",
    "CandidateInput",
    "ConfidenceBand",
    "ConfidenceConfig",
    "ConfidenceWeights",
    "EmbeddingDimMismatchError",
    "FuzzyDedupeConfig",
    "FuzzyDedupeResult",
    "FuzzyReviewAlreadyDecidedError",
    "FuzzyReviewNotFoundError",
    "FuzzyReviewSignalMissingError",
    "IcpCriteria",
    "RelatedSignal",
    "ScoreResult",
    "ScoringConfig",
    "SignalDimensions",
    "SignalFuzzyReview",
    "SignalPage",
    "SignalPayload",
    "SignalRead",
    "SignalSourceDocument",
    "SignalType",
    "SignalValidationError",
    "SuggestedContact",
    "WorkspaceFeedItem",
    "WorkspaceFeedPage",
    "WorkspaceScore",
    "WorkspaceScoreResult",
    "WorkspaceSignalDetail",
    "backfill_embeddings",
    "build_embedding_text",
    "candidate_icp_filter",
    "candidate_signal_ids_for_icp",
    "compute_dedupe_hash",
    "compute_dedupe_hash_for_payload",
    "config_from_recipe",
    "decide_fuzzy_review",
    "decode_cursor",
    "dedupe_key_for_candidate",
    "embed_signals",
    "embedding_text_for_signal",
    "encode_cursor",
    "find_duplicate",
    "get_fuzzy_review",
    "get_signal",
    "get_signal_detail",
    "is_high_stakes_type",
    "list_fuzzy_reviews",
    "list_signals",
    "list_workspace_signals",
    "merge_signal",
    "parse_signal_payload",
    "promote_candidate_to_signal",
    "run_fuzzy_dedupe",
    "score_candidate_confidence",
    "score_signal_against_icp",
    "score_signal_for_all_workspaces",
    "score_signal_for_workspace",
    "score_workspace_candidates",
    "signal_matches_icp",
    "store_signal",
    "window_for",
]
