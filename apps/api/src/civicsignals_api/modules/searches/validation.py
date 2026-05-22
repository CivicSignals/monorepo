"""Centralized saved-search filter-validation rules (H2).

A saved search stores a subset of the G1 feed filter params
(``signals.list_workspace_signals``) as a JSON blob (H1, see
:mod:`~searches.schemas`). H2 makes the *invalid / contradictory* filter
combinations fail with an **explicit, human-readable message per violated rule**
— not a generic "invalid" — and centralizes those rules here so create and
update share one source of truth (PRD H2 acceptance criteria).

The rule engine is **pure** (no DB, no Pydantic): :func:`validate_filters` takes
the raw filter mapping (the JSON the client sent) and returns a list of
:class:`FilterViolation`, one per rule that failed. Returning *all* violations
(rather than stopping at the first) lets the API surface every problem at once,
and lets the create/update routes render them as RFC 7807 ``errors[]`` (doc 08
§1.7) with one clear message each.

Both surfaces consume this engine:

- :class:`~searches.schemas.SearchFilters` runs it in its validators so a request
  body fails with these explicit messages (the route validates the body through
  the model, so FastAPI's ``RequestValidationError`` handler renders them).
- The routes can also raise :class:`FilterValidationError` directly to emit the
  same problem shape when validating a raw blob.

The enum domains (signal-type taxonomy, feed statuses) are sourced from the
*signals* module's public service surface so a rule can only reject a value the
feed itself would reject — searches reaches signals only via ``services`` (doc
06 §3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from civicsignals_api.modules.signals import services as signals_services

# Enum domains, sourced from the signals module's public surface (doc 06 §3).
VALID_SIGNAL_TYPES: frozenset[str] = frozenset(t.value for t in signals_services.SignalType)
VALID_STATUSES: frozenset[str] = frozenset(signals_services.SCORE_STATUSES)

# The closed set of filter keys a saved search may carry. A key outside this set
# is a stray/unknown filter the feed cannot honour, so it is rejected at the edge
# (mirrors ``SearchFilters``'s ``extra="forbid"``).
ALLOWED_FILTER_KEYS: frozenset[str] = frozenset(
    {
        "signal_type",
        "statuses",
        "min_score",
        "published_at_gte",
        "published_at_lt",
    }
)

# Score is on the 0..100 scale (doc 14 §5.3 ``WorkspaceScore.score``).
MIN_SCORE_FLOOR = 0.0
MIN_SCORE_CEIL = 100.0


@dataclass(frozen=True, slots=True)
class FilterViolation:
    """One violated filter rule, as a field-scoped, human-readable message.

    ``field`` is the offending filter key (or ``"filters"`` for a whole-blob /
    cross-field rule) so the UI can attach the message inline; ``code`` is a
    stable machine slug an SDK / test can branch on; ``message`` is the explicit
    sentence shown to the user.
    """

    field: str
    code: str
    message: str

    def as_error(self) -> dict[str, str]:
        """Render as an RFC 7807 ``errors[]`` entry (doc 08 §1.7)."""
        return {"field": self.field, "code": self.code, "message": self.message}


class FilterValidationError(ValueError):
    """One or more filter rules failed; carries every :class:`FilterViolation`.

    Routes translate this into a ``422`` RFC 7807 problem whose ``errors[]`` is
    one entry per violation, so the client sees a clear message per broken rule.

    Subclasses :class:`ValueError` so that if it is raised inside a Pydantic
    validator (when a :class:`~searches.schemas.SearchFilters` is constructed
    directly) Pydantic wraps it into a :class:`pydantic.ValidationError` with the
    joined explicit messages — the route path validates the raw blob first and
    catches this type directly for the per-rule ``errors[]``.
    """

    def __init__(self, violations: list[FilterViolation]) -> None:
        self.violations = violations
        joined = "; ".join(v.message for v in violations) or "invalid filters"
        super().__init__(joined)


def _coerce_dt(value: Any) -> datetime | None:
    """Best-effort parse of a datetime that may arrive as an ISO string or datetime.

    The raw blob can carry either a ``datetime`` (constructed in Python) or an
    ISO-8601 string (deserialized JSON). Returns ``None`` when the value is not a
    parseable datetime — the *type* check is a separate rule, so an unparseable
    value simply means the cross-field date-range rule cannot apply.
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Rules — each takes the raw filter mapping and appends 0+ explicit violations.
# Keeping them as small named functions makes the rule set self-documenting and
# individually unit-testable (one test per rule, per H2's test requirement).
# ---------------------------------------------------------------------------


def _rule_unknown_keys(raw: Mapping[str, Any], out: list[FilterViolation]) -> None:
    """Reject stray/unknown filter keys (the feed cannot honour them)."""
    for key in raw:
        if key not in ALLOWED_FILTER_KEYS:
            out.append(
                FilterViolation(
                    field=key,
                    code="unknown_filter",
                    message=(
                        f"“{key}” is not a filter you can save. "
                        f"Allowed filters: {', '.join(sorted(ALLOWED_FILTER_KEYS))}."
                    ),
                )
            )


def _rule_signal_type(raw: Mapping[str, Any], out: list[FilterViolation]) -> None:
    """Reject an unknown signal-type slug (must be in the canonical taxonomy)."""
    value = raw.get("signal_type")
    if value is None:
        return
    if not isinstance(value, str) or value not in VALID_SIGNAL_TYPES:
        out.append(
            FilterViolation(
                field="signal_type",
                code="unknown_signal_type",
                message=(
                    f"“{value}” is not a known signal type. "
                    f"Choose one of: {', '.join(sorted(VALID_SIGNAL_TYPES))}."
                ),
            )
        )


def _rule_statuses(raw: Mapping[str, Any], out: list[FilterViolation]) -> None:
    """Validate the statuses list: right type, non-empty if present, known values.

    An empty ``statuses`` list is a contradictory selection — it names *no*
    status bucket, so the feed would return nothing; a saved search that can
    never match is rejected with an explicit message (omit ``statuses`` entirely
    to mean "all default statuses").
    """
    value = raw.get("statuses")
    if value is None:
        return
    if not isinstance(value, list):
        out.append(
            FilterViolation(
                field="statuses",
                code="statuses_not_a_list",
                message="Statuses must be a list of status values.",
            )
        )
        return
    if len(value) == 0:
        out.append(
            FilterViolation(
                field="statuses",
                code="statuses_empty",
                message=(
                    "Select at least one status, or remove the status filter to "
                    "include all of them — an empty status list matches nothing."
                ),
            )
        )
        return
    unknown = [s for s in value if not isinstance(s, str) or s not in VALID_STATUSES]
    if unknown:
        rendered = ", ".join(str(s) for s in unknown)
        out.append(
            FilterViolation(
                field="statuses",
                code="unknown_status",
                message=(
                    f"Unknown status: {rendered}. "
                    f"Valid statuses: {', '.join(sorted(VALID_STATUSES))}."
                ),
            )
        )


def _rule_min_score(raw: Mapping[str, Any], out: list[FilterViolation]) -> None:
    """Reject a min-score outside the 0..100 scale (out-of-range threshold)."""
    value = raw.get("min_score")
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        out.append(
            FilterViolation(
                field="min_score",
                code="min_score_not_a_number",
                message="Minimum score must be a number between 0 and 100.",
            )
        )
        return
    if value < MIN_SCORE_FLOOR or value > MIN_SCORE_CEIL:
        out.append(
            FilterViolation(
                field="min_score",
                code="min_score_out_of_range",
                message=(
                    f"Minimum score must be between {MIN_SCORE_FLOOR:g} and "
                    f"{MIN_SCORE_CEIL:g} (got {value:g})."
                ),
            )
        )


def _rule_date_types(raw: Mapping[str, Any], out: list[FilterViolation]) -> None:
    """Reject a date bound that is present but not a parseable datetime."""
    for key in ("published_at_gte", "published_at_lt"):
        if key not in raw or raw[key] is None:
            continue
        if _coerce_dt(raw[key]) is None:
            out.append(
                FilterViolation(
                    field=key,
                    code="invalid_date",
                    message=f"“{key}” must be a valid ISO-8601 date/time.",
                )
            )


def _rule_date_range(raw: Mapping[str, Any], out: list[FilterViolation]) -> None:
    """Reject an inverted/empty date range (start must be strictly before end).

    Only applies when both bounds are present *and* both parse — the type rule
    (:func:`_rule_date_types`) already flags an unparseable bound, so this stays
    a clean cross-field check. Start == end is an empty window (``gte``/``lt`` is
    a half-open interval), so equal bounds are also rejected.
    """
    gte = _coerce_dt(raw.get("published_at_gte"))
    lt = _coerce_dt(raw.get("published_at_lt"))
    if gte is None or lt is None:
        return
    if gte >= lt:
        out.append(
            FilterViolation(
                field="published_at_gte",
                code="date_range_inverted",
                message=(
                    "The start date must be before the end date "
                    "(published_at_gte must be earlier than published_at_lt)."
                ),
            )
        )


# Ordered so the surfaced messages read predictably: structural (unknown keys),
# then per-field, then cross-field (date range last).
_RULES = (
    _rule_unknown_keys,
    _rule_signal_type,
    _rule_statuses,
    _rule_min_score,
    _rule_date_types,
    _rule_date_range,
)


def validate_filters(raw: Mapping[str, Any]) -> list[FilterViolation]:
    """Run every filter rule over the raw blob; return all violations (may be empty).

    Pure and side-effect-free. The caller decides what to do with the result:
    :class:`~searches.schemas.SearchFilters` raises on a non-empty list, and the
    routes turn it into an RFC 7807 ``422`` with one ``errors[]`` entry per
    violation.
    """
    violations: list[FilterViolation] = []
    for rule in _RULES:
        rule(raw, violations)
    return violations


def assert_valid_filters(raw: Mapping[str, Any]) -> None:
    """Raise :class:`FilterValidationError` if any filter rule is violated."""
    violations = validate_filters(raw)
    if violations:
        raise FilterValidationError(violations)


__all__ = [
    "ALLOWED_FILTER_KEYS",
    "MIN_SCORE_CEIL",
    "MIN_SCORE_FLOOR",
    "VALID_SIGNAL_TYPES",
    "VALID_STATUSES",
    "FilterValidationError",
    "FilterViolation",
    "assert_valid_filters",
    "validate_filters",
]
