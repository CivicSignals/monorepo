# SPDX-License-Identifier: AGPL-3.0-only
"""The per-workspace signal matcher + scorer (F3, doc 14 §6).

This is the linchpin that turns the **global** signal corpus into a per-workspace
ranked feed (G1): for each new signal, find the workspaces whose ICP it *could*
match (the cheap pre-filter, doc 14 §6.1), then full-score it against each
candidate ICP and write a sparse ``signals_workspace_score`` row when the score
clears the workspace's threshold (doc 14 §6.2).

**Two stages, two costs** (doc 14 §6.3):

1. **Matcher** (cheap pre-filter) — a set-intersection over the ICP's pre-indexed
   dimension columns (countries / states / entity_kinds / signal_types GIN arrays +
   the size band, doc 14 §6.1). An empty ICP dimension array means "all values".
   This narrows 5,000 workspaces to a handful of candidates with one indexed SQL
   query; it is implemented in ``signals.services`` (it needs the DB). The matcher's
   *predicate* — "does this signal's dimensions satisfy this ICP?" — is the pure
   :func:`signal_matches_icp` here, used by both the SQL pre-filter's caller and the
   tests.

2. **Scorer** (full score) — the more expensive per-candidate evaluation
   (doc 14 §6.2): blend the ICP's per-signal-type weight, dimension-match strength,
   recency, the signal's extraction confidence (E6), keyword match (required /
   excluded), and optional semantic similarity (I1 embeddings vs an ICP-keyword
   embedding) into a 0..100 score, and emit the **component breakdown** F4 renders.

This module is **pure** — no DB, no I/O, no LLM calls — so the matcher predicate and
the blend math are trivially unit-testable, mirroring the E6 ``signals.scoring``
shape. The DB-bound matcher query, the fan-out, and the upsert live in
``signals.services``; the optional embedding fetch is the caller's job (the I1
``embed()`` gateway call), passed in as plain vectors here.

# TODO F5: the feedback loop (relevant / not_relevant / wrong_extraction) re-weights
# subsequent scores for a workspace (doc 14 §12 "negative training"). The seam is the
# per-workspace weight overrides — :class:`ScoringConfig` carries the knobs; F5 will
# populate them from accumulated feedback rather than the static defaults.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

# --- Component weights (the relative pull of each scorer dimension, doc 14 §6.2) --
#
# These weight the *blend* of the five scorer components into the final 0..100
# score. They are deliberately a separate concept from the ICP's per-signal-type
# weights (``signal_weights``) — those scale the signal-type component; these decide
# how much each component (type / dimensions / recency / confidence / keywords /
# semantic) contributes overall. They sum to 1.0 (validated) so the blend is a
# weighted average mapped onto 0..100.

DEFAULT_WEIGHT_SIGNAL_TYPE: Final = 0.30
DEFAULT_WEIGHT_DIMENSIONS: Final = 0.25
DEFAULT_WEIGHT_RECENCY: Final = 0.10
DEFAULT_WEIGHT_CONFIDENCE: Final = 0.10
DEFAULT_WEIGHT_KEYWORDS: Final = 0.20
DEFAULT_WEIGHT_SEMANTIC: Final = 0.05

# The maximum score (doc 14: F1 threshold is on the 0..100 scale).
MAX_SCORE: Final = 100.0

# Recency half-life: a signal this many days old contributes half the recency
# component of a brand-new one (doc 14 §6.2 recency). 30 days keeps the feed fresh
# without hard-cutting older-but-relevant signals.
RECENCY_HALF_LIFE_DAYS: Final = 30.0

# A signal with no extraction confidence (E6 not run / non-pipeline insert) gets a
# neutral confidence component — a missing score neither inflates nor tanks it.
NEUTRAL_CONFIDENCE: Final = 0.5

# The neutral default for the signal-type-weight component when the ICP did not
# pin a weight for this signal type. The ICP listed the type in ``signal_types``
# (else the matcher would have excluded the signal), so it is wanted — just not
# explicitly weighted; treat it as moderately wanted rather than 0.
DEFAULT_SIGNAL_TYPE_WEIGHT: Final = 0.6


class KeywordExcludedError(Exception):
    """A required-to-exclude keyword was present — the signal is hard-dropped.

    Distinct from "scored below threshold": an excluded keyword (doc 14 §3.1
    ``must_not_match``) means the workspace explicitly does **not** want this
    signal, so it earns no score row regardless of the other dimensions. The
    service catches this and writes nothing (and removes any stale row on a
    re-score / backfill).
    """


@dataclass(frozen=True, slots=True)
class ComponentWeights:
    """How much each scorer component pulls on the final blend (doc 14 §6.2).

    A custom set must still sum to ~1.0 (the blend stays a weighted average);
    :meth:`__post_init__` validates that and non-negativity, raising ``ValueError``
    on a malformed override — mirroring E6's :class:`ConfidenceWeights`.
    """

    signal_type: float = DEFAULT_WEIGHT_SIGNAL_TYPE
    dimensions: float = DEFAULT_WEIGHT_DIMENSIONS
    recency: float = DEFAULT_WEIGHT_RECENCY
    confidence: float = DEFAULT_WEIGHT_CONFIDENCE
    keywords: float = DEFAULT_WEIGHT_KEYWORDS
    semantic: float = DEFAULT_WEIGHT_SEMANTIC

    def __post_init__(self) -> None:
        weights = (
            self.signal_type,
            self.dimensions,
            self.recency,
            self.confidence,
            self.keywords,
            self.semantic,
        )
        if any(w < 0 for w in weights):
            raise ValueError("component weights must be non-negative")
        total = sum(weights)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"component weights must sum to 1.0 (got {total})")


@dataclass(frozen=True, slots=True)
class ScoringConfig:
    """Tunables for the scorer (doc 14 §6.2).

    ``weights`` is the component blend; ``recency_half_life_days`` controls how fast
    the recency component decays. ``DEFAULT_SCORING_CONFIG`` is the documented
    baseline. F5's feedback loop will derive per-workspace overrides from accumulated
    feedback (doc 14 §12) — the config is the seam it writes through.
    """

    weights: ComponentWeights = field(default_factory=ComponentWeights)
    recency_half_life_days: float = RECENCY_HALF_LIFE_DAYS

    def __post_init__(self) -> None:
        if self.recency_half_life_days <= 0:
            raise ValueError("recency_half_life_days must be > 0")


#: The documented §6.2 baseline used when no per-workspace override exists.
DEFAULT_SCORING_CONFIG: Final = ScoringConfig()


@dataclass(frozen=True, slots=True)
class SignalDimensions:
    """The matchable dimensions a new signal carries (doc 14 §6.1).

    Resolved by the service from the ``signals_signal`` row joined to its
    ``entities_entity`` (country / state / kind / size). Any field may be ``None``
    when the entity is unresolved (doc 19 §4.3) or the attribute is unknown — the
    matcher treats an unknown signal dimension as failing only the ICP dimensions
    that *restrict* it (an ICP that does not restrict that dimension still matches).
    """

    signal_type: str
    country: str | None = None
    state: str | None = None
    entity_kind: str | None = None
    size: int | None = None


@dataclass(frozen=True, slots=True)
class IcpCriteria:
    """The subset of an ICP the matcher + scorer read (doc 14 §3.1, §6).

    A plain snapshot decoupled from the ``icp`` module's ORM row so this module
    stays pure and importable without a DB. The service builds it from the
    ``IcpDefinition`` it loaded via ``icp.services.get_active_icp``. Empty dimension
    arrays mean "all values" (doc 14 §6.1).
    """

    signal_types: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    states: tuple[str, ...] = ()
    entity_kinds: tuple[str, ...] = ()
    min_size: int | None = None
    max_size: int | None = None
    signal_weights: Mapping[str, float] = field(default_factory=dict)
    keywords_required: tuple[str, ...] = ()
    keywords_excluded: tuple[str, ...] = ()
    threshold: float = 50.0


@dataclass(frozen=True, slots=True)
class DimensionMatch:
    """Which ICP dimensions a candidate satisfied (the matched flags, doc 14 §6.2).

    ``matched_states`` keeps the actual intersecting states so F4 can name them.
    ``strength`` is the fraction of *restricting* dimensions the signal satisfied,
    in [0, 1] — the dimension-match-strength component of the score (doc 14 §6.2).
    """

    signal_type: bool
    country: bool
    state: bool
    entity_kind: bool
    size_band: bool
    matched_states: tuple[str, ...]
    strength: float


@dataclass(frozen=True, slots=True)
class WorkspaceScoreResult:
    """The outcome of scoring one signal against one ICP (doc 14 §6.2).

    ``score`` is the blended 0..100 relevance; ``breakdown`` is the per-component
    contribution map persisted for F4's "Why this signal?"; ``matched`` is the
    dimension flags; ``matched_keywords`` are the required keywords that hit.
    ``passes_threshold`` is the gate the service applies before writing a row.
    """

    score: float
    breakdown: dict[str, Any]
    matched: DimensionMatch
    matched_keywords: tuple[str, ...]
    threshold: float

    @property
    def passes_threshold(self) -> bool:
        """Whether the score clears the ICP threshold → earns a score row (§6.2)."""
        return self.score >= self.threshold


# ---------------------------------------------------------------------------
# Matcher (pure predicate — the SQL pre-filter mirrors this, doc 14 §6.1)
# ---------------------------------------------------------------------------


def _dimension_allows(icp_values: Sequence[str], signal_value: str | None) -> bool:
    """Whether an ICP array dimension *allows* a signal value (doc 14 §6.1).

    Empty ICP array == "all values" → always allows. Otherwise the signal value
    must be a member. An unknown (``None``) signal value can only satisfy an
    unrestricted dimension — a restricting ICP excludes the signal (we cannot
    confirm the dimension matches), mirroring the SQL ``(states = '{}' OR states
    @> ARRAY[$state])`` where a NULL ``$state`` fails the ``@>``.
    """
    if not icp_values:
        return True
    if signal_value is None:
        return False
    return signal_value in icp_values


def _size_in_band(icp: IcpCriteria, size: int | None) -> bool:
    """Whether a signal's entity size falls in the ICP band (doc 14 §6.1).

    An ICP with no band (both bounds ``None``) accepts any size — including an
    unknown one. A *restricting* band requires a known size within it; an unknown
    size against a restricting band fails (same NULL-excludes rule as the array
    dimensions).
    """
    if icp.min_size is None and icp.max_size is None:
        return True
    if size is None:
        return False
    if icp.min_size is not None and size < icp.min_size:
        return False
    return not (icp.max_size is not None and size > icp.max_size)


def signal_matches_icp(signal: SignalDimensions, icp: IcpCriteria) -> bool:
    """The cheap pre-filter predicate (doc 14 §6.1): could this signal match this ICP?

    True iff the signal satisfies **every** dimension the ICP *restricts*
    (signal_type / country / state / entity_kind / size band). Empty ICP dimensions
    are unrestricted (all values). This is the exact predicate the service's SQL
    candidate query encodes against the GIN-indexed ICP columns — kept here as a pure
    function so the matcher's behaviour is unit-tested without a DB and the keyword /
    deal-band / score work is left to the (more expensive) scorer (doc 14 §6.2).
    """
    return (
        _dimension_allows(icp.signal_types, signal.signal_type)
        and _dimension_allows(icp.countries, signal.country)
        and _dimension_allows(icp.states, signal.state)
        and _dimension_allows(icp.entity_kinds, signal.entity_kind)
        and _size_in_band(icp, signal.size)
    )


def _dimension_match(signal: SignalDimensions, icp: IcpCriteria) -> DimensionMatch:
    """Compute the matched flags + dimension-match strength (doc 14 §6.2).

    A dimension counts toward strength only when the ICP *restricts* it (a
    non-empty array / a band) — an unrestricted dimension is neither a match nor a
    miss, so it does not dilute the strength of the dimensions the workspace
    actually cares about. ``strength`` is the fraction of restricting dimensions the
    signal satisfied; with no restricting dimensions it is a neutral 1.0 (the ICP
    targets broadly, so any in-scope signal is fully on-dimension).
    """
    restricting = 0
    satisfied = 0

    type_restricts = bool(icp.signal_types)
    type_ok = _dimension_allows(icp.signal_types, signal.signal_type)
    if type_restricts:
        restricting += 1
        satisfied += int(type_ok)

    country_restricts = bool(icp.countries)
    country_ok = _dimension_allows(icp.countries, signal.country)
    if country_restricts:
        restricting += 1
        satisfied += int(country_ok)

    state_restricts = bool(icp.states)
    state_ok = _dimension_allows(icp.states, signal.state)
    if state_restricts:
        restricting += 1
        satisfied += int(state_ok)

    kind_restricts = bool(icp.entity_kinds)
    kind_ok = _dimension_allows(icp.entity_kinds, signal.entity_kind)
    if kind_restricts:
        restricting += 1
        satisfied += int(kind_ok)

    size_restricts = icp.min_size is not None or icp.max_size is not None
    size_ok = _size_in_band(icp, signal.size)
    if size_restricts:
        restricting += 1
        satisfied += int(size_ok)

    matched_states: tuple[str, ...] = ()
    if state_ok and icp.states and signal.state is not None:
        matched_states = (signal.state,)

    strength = 1.0 if restricting == 0 else satisfied / restricting
    return DimensionMatch(
        signal_type=type_ok,
        country=country_ok,
        state=state_ok,
        entity_kind=kind_ok,
        size_band=size_ok,
        matched_states=matched_states,
        strength=strength,
    )


# ---------------------------------------------------------------------------
# Scorer component helpers (each pure, each in [0, 1] before weighting)
# ---------------------------------------------------------------------------


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, value))


def _signal_type_weight(icp: IcpCriteria, signal_type: str) -> float:
    """The ICP's per-signal-type weight (doc 14 §3.1 ``custom_weights``, §6.2).

    Falls back to :data:`DEFAULT_SIGNAL_TYPE_WEIGHT` when the ICP listed the type as
    of-interest but pinned no explicit weight — the type is wanted, just unweighted.
    """
    raw = icp.signal_weights.get(signal_type)
    if raw is None:
        return DEFAULT_SIGNAL_TYPE_WEIGHT
    return _clamp_unit(float(raw))


def recency_score(observed_at: datetime | None, *, now: datetime, half_life_days: float) -> float:
    """Exponential recency decay in [0, 1] (doc 14 §6.2 recency).

    A brand-new signal scores ~1.0; one ``half_life_days`` old scores 0.5; older
    signals decay smoothly toward 0. A future or missing timestamp is treated as
    "now" (1.0) — clock skew / a missing observed_at should not penalise a signal.
    """
    if observed_at is None:
        return 1.0
    obs = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=UTC)
    age_days = (now - obs).total_seconds() / 86_400.0
    if age_days <= 0:
        return 1.0
    return _clamp_unit(math.pow(0.5, age_days / half_life_days))


def _text_contains(haystack: str, needle: str) -> bool:
    """Case-insensitive substring match (the MVP keyword check, doc 14 §6.2).

    Doc 14 §6.2 envisions BM25/FTS; the MVP uses a cheap case-insensitive substring
    over the title + summary (+ a few details fields) — the same surface the
    embedding text uses. The FTS upgrade is a later tuning pass.
    """
    return needle.lower() in haystack.lower()


def keyword_match(
    text_blob: str,
    *,
    required: Sequence[str],
    excluded: Sequence[str],
) -> tuple[float, tuple[str, ...]]:
    """Score keyword fit + return the required keywords that hit (doc 14 §6.2).

    Returns ``(score in [0, 1], matched_required_keywords)``. Raises
    :class:`KeywordExcludedError` if any excluded keyword is present — an excluded
    keyword is a hard drop (doc 14 §3.1 ``must_not_match``), not a soft penalty.

    With no required keywords the component is a neutral 1.0 (the workspace did not
    constrain by keyword, so keywords neither help nor hurt). With required keywords
    the score is the fraction that matched — and a *required* set with **zero** hits
    is a hard drop too: the ICP's ``must_match_any`` was not satisfied at all.
    """
    for kw in excluded:
        if kw and _text_contains(text_blob, kw):
            raise KeywordExcludedError(f"excluded keyword present: {kw!r}")

    cleaned_required = [kw for kw in required if kw]
    if not cleaned_required:
        return 1.0, ()

    hits = tuple(kw for kw in cleaned_required if _text_contains(text_blob, kw))
    if not hits:
        raise KeywordExcludedError("no required keyword matched (must_match_any not satisfied)")
    return len(hits) / len(cleaned_required), hits


def semantic_similarity(
    signal_vector: Sequence[float] | None,
    icp_vector: Sequence[float] | None,
) -> float | None:
    """Cosine similarity (mapped to [0, 1]) of the signal vs ICP embedding (§6.2).

    Optional (doc 14 §6.2 / §12 v2): returns ``None`` when either embedding is
    absent so the blend can renormalise around the remaining components rather than
    treating "no embedding" as a 0 similarity. Cosine in [-1, 1] is mapped to [0, 1]
    via ``(cos + 1) / 2`` so an orthogonal pair lands at the neutral 0.5.
    """
    if not signal_vector or not icp_vector or len(signal_vector) != len(icp_vector):
        return None
    dot = sum(a * b for a, b in zip(signal_vector, icp_vector, strict=True))
    na = math.sqrt(sum(a * a for a in signal_vector))
    nb = math.sqrt(sum(b * b for b in icp_vector))
    if na == 0.0 or nb == 0.0:
        return None
    cos = dot / (na * nb)
    return _clamp_unit((cos + 1.0) / 2.0)


# ---------------------------------------------------------------------------
# The blend
# ---------------------------------------------------------------------------


def score_signal_against_icp(
    signal: SignalDimensions,
    icp: IcpCriteria,
    *,
    text_blob: str,
    confidence: float | None = None,
    observed_at: datetime | None = None,
    signal_vector: Sequence[float] | None = None,
    icp_vector: Sequence[float] | None = None,
    config: ScoringConfig = DEFAULT_SCORING_CONFIG,
    now: datetime | None = None,
) -> WorkspaceScoreResult:
    """Full-score one signal against one ICP → a 0..100 score + breakdown (doc 14 §6.2).

    Blends the six components (signal-type weight, dimension-match strength, recency,
    extraction confidence, keyword match, optional semantic similarity) with the
    configured weights. The semantic component is dropped (and the remaining weights
    renormalised) when no embedding pair is available, so a workspace without an ICP
    embedding is scored on the structured + keyword signal alone.

    Raises :class:`KeywordExcludedError` when an excluded keyword is present or a
    required-keyword set matched nothing — a hard drop the caller turns into "no row"
    (doc 14 §3.1). Otherwise returns a :class:`WorkspaceScoreResult`; the caller
    applies the ICP threshold via :attr:`WorkspaceScoreResult.passes_threshold`.

    The ``breakdown`` records each component's raw [0, 1] value and its weighted
    points contribution toward the 0..100 total, plus human-readable ``bullets`` —
    everything F4's "Why this signal?" panel needs without re-running the scorer.
    """
    now = now or datetime.now(UTC)
    weights = config.weights

    type_weight = _signal_type_weight(icp, signal.signal_type)
    dims = _dimension_match(signal, icp)
    recency = recency_score(observed_at, now=now, half_life_days=config.recency_half_life_days)
    conf = NEUTRAL_CONFIDENCE if confidence is None else _clamp_unit(confidence)
    kw_score, matched_keywords = keyword_match(
        text_blob, required=icp.keywords_required, excluded=icp.keywords_excluded
    )
    semantic = semantic_similarity(signal_vector, icp_vector)

    # Component (raw value, weight) pairs. Semantic is omitted entirely when absent
    # so the blend renormalises around the present components (doc 14 §6.2 optional).
    components: list[tuple[str, float, float]] = [
        ("signal_type_weight", type_weight, weights.signal_type),
        ("dimensions", dims.strength, weights.dimensions),
        ("recency", recency, weights.recency),
        ("confidence", conf, weights.confidence),
        ("keywords", kw_score, weights.keywords),
    ]
    if semantic is not None:
        components.append(("semantic", semantic, weights.semantic))

    total_weight = sum(w for _, _, w in components)
    breakdown_components: dict[str, dict[str, float]] = {}
    score_unit = 0.0
    for name, value, weight in components:
        norm_weight = weight / total_weight if total_weight > 0 else 0.0
        contribution = value * norm_weight
        score_unit += contribution
        breakdown_components[name] = {
            "value": round(value, 4),
            "weight": round(norm_weight, 4),
            "points": round(contribution * MAX_SCORE, 2),
        }

    score = round(_clamp_unit(score_unit) * MAX_SCORE, 2)
    bullets = _explanation_bullets(signal, dims, matched_keywords, conf, recency)
    breakdown: dict[str, Any] = {
        "components": breakdown_components,
        "matched": {
            "signal_type": dims.signal_type,
            "country": dims.country,
            "state": dims.state,
            "entity_kind": dims.entity_kind,
            "size_band": dims.size_band,
            "states": list(dims.matched_states),
            "keywords": list(matched_keywords),
        },
        "bullets": bullets,
    }
    return WorkspaceScoreResult(
        score=score,
        breakdown=breakdown,
        matched=dims,
        matched_keywords=matched_keywords,
        threshold=icp.threshold,
    )


def _explanation_bullets(
    signal: SignalDimensions,
    dims: DimensionMatch,
    matched_keywords: Sequence[str],
    confidence: float,
    recency: float,
) -> list[str]:
    """Build the human-readable "Why this signal?" bullets (doc 14 §6.2; F4 seam).

    F4 owns the final UI copy; this seeds the structured bullets from the matched
    dimensions so the panel has content the moment a score row exists.
    """
    bullets: list[str] = []
    if dims.signal_type and signal.signal_type:
        bullets.append(f"signal type {signal.signal_type} is of interest")
    for state in dims.matched_states:
        bullets.append(f"matched state {state}")
    if dims.entity_kind and signal.entity_kind:
        bullets.append(f"matched entity kind {signal.entity_kind}")
    if dims.size_band and signal.size is not None:
        bullets.append(f"entity size {signal.size} in target band")
    for kw in matched_keywords:
        bullets.append(f"keyword {kw!r} in signal text")
    if recency >= 0.5:
        bullets.append("recently observed")
    if confidence >= 0.8:
        bullets.append("high extraction confidence")
    return bullets


__all__ = [
    "DEFAULT_SCORING_CONFIG",
    "MAX_SCORE",
    "ComponentWeights",
    "DimensionMatch",
    "IcpCriteria",
    "KeywordExcludedError",
    "ScoringConfig",
    "SignalDimensions",
    "WorkspaceScoreResult",
    "keyword_match",
    "recency_score",
    "score_signal_against_icp",
    "semantic_similarity",
    "signal_matches_icp",
]
