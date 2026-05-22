"""Public service interface for the recipes module.

Other modules call recipes only through the functions defined here — never by
importing the recipes module's models or routes directly (doc 06 §3).

This module owns the **recipe DSL** and the **recipe runner** (doc 18 §2-§3,
TODO D1): loading + JSON-Schema-validating recipe YAML, and driving the
``discover -> fetch -> extract -> normalize`` lifecycle through an injectable
fetcher. The ingestion module's Celery tasks (TODO D4/D6) call into here rather
than reaching into the runner internals.

The **authoring tooling** (TODO D5) layers on top: dry-run *preview* of a recipe
against pasted HTML or a live URL, plus a *scaffold* helper. The CLI and the
staff preview endpoint both go through the functions here.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from civicsignals_api.config import get_settings as _get_settings

from .drift import (
    WINDOW_7D_HOURS,
    WINDOW_24H_HOURS,
    build_run_outcome,
    clear_drift_state,
    compute_rolling_metrics,
    evaluate_recipe_drift,
    evaluation_subjects,
    get_drift_state,
    list_recipe_ids_with_runs,
    record_drift_pause,
    record_run_outcome,
    rollup_metrics,
)
from .fixtures import (
    list_recipe_ids,
    replay_fixture,
    replay_recipe,
)
from .github_client import (
    GitHubClient,
    HttpxGitHubClient,
    NoopGitHubClient,
)
from .http_fetcher import HttpxFetcher
from .models import DeadLetter
from .preview import preview_html, preview_url
from .runner import (
    Clock,
    Fetcher,
    FetchFailedError,
    GatewayFieldExtractor,
    LLMFieldExtractor,
    RealClock,
    RecipeError,
    RecipeNotFoundError,
    RecipeRunner,
    RecipeValidationError,
    RequiredFieldMissingError,
    RobotsDisallowedError,
    load_recipe,
    load_recipe_file,
    parse_recipe,
    parse_recipe_yaml,
    recipes_dir,
    validate_recipe_data,
)
from .scaffold import render_recipe, scaffold_recipe
from .schemas import (
    CanonicalRecord,
    DeadLetterEntry,
    DriftCounters,
    DriftEvaluation,
    DriftStateRecord,
    ExtractedDocument,
    FieldExtraction,
    FieldPreview,
    FixtureReplayResult,
    PreviewRequest,
    PreviewResult,
    Recipe,
    RollingMetrics,
    RunOutcome,
    SourcePointer,
)


def make_runner(
    recipe: Recipe,
    fetcher: Fetcher,
    *,
    clock: Clock | None = None,
    llm_extractor: LLMFieldExtractor | None = None,
) -> RecipeRunner:
    """Construct a :class:`RecipeRunner` for an already-parsed recipe.

    ``llm_extractor`` overrides the gateway-backed LLM-assisted fallback rung
    (doc 18 §3.4) — primarily a test seam; production leaves it ``None`` so the
    runner lazily uses :class:`GatewayFieldExtractor` only for fields that opt in.
    """
    return RecipeRunner(recipe, fetcher, clock=clock, llm_extractor=llm_extractor)


def run_recipe(
    recipe_id: str,
    fetcher: Fetcher,
    seed_urls: Sequence[str],
    *,
    clock: Clock | None = None,
    llm_extractor: LLMFieldExtractor | None = None,
) -> list[CanonicalRecord]:
    """Load recipe ``recipe_id`` and run its full lifecycle over ``seed_urls``."""
    recipe = load_recipe(recipe_id)
    runner = RecipeRunner(recipe, fetcher, clock=clock, llm_extractor=llm_extractor)
    return runner.run(seed_urls)


def run_pointers(
    recipe: Recipe,
    fetcher: Fetcher,
    pointers: Sequence[SourcePointer],
    *,
    clock: Clock | None = None,
    llm_extractor: LLMFieldExtractor | None = None,
) -> list[CanonicalRecord]:
    """Run ``fetch -> extract -> normalize`` over connector-discovered pointers.

    The cross-module seam the ingestion connectors (D6) use: a connector computes
    the source-type-specific pointer set via its own ``discover`` and the runner
    drives the rest of the lifecycle (robots/politeness, version pinning, the
    ordered extract fallback chain). Keeps ``discover`` connector-owned without the
    recipes module importing ingestion (doc 06 §3).
    """
    runner = RecipeRunner(recipe, fetcher, clock=clock, llm_extractor=llm_extractor)
    return runner.run_pointers(pointers)


def run_pointers_with_outcome(
    recipe: Recipe,
    fetcher: Fetcher,
    pointers: Sequence[SourcePointer],
    *,
    clock: Clock | None = None,
    llm_extractor: LLMFieldExtractor | None = None,
    wall_clock_seconds: float | None = None,
) -> tuple[list[CanonicalRecord], RunOutcome]:
    """Run pointers and also build the E7 :class:`RunOutcome` from the result.

    The drift-recording seam (doc 18 §3.2): runs the same lifecycle as
    :func:`run_pointers` but threads out each per-document extraction and folds the
    D11 drift counters into a per-run outcome — *without* re-running the chain or
    editing the pipeline stages. The ingest worker (D4) records the returned outcome
    via :func:`record_run_outcome` so the rolling-window drift detection has data.
    """
    runner = RecipeRunner(recipe, fetcher, clock=clock, llm_extractor=llm_extractor)
    records, extractions = runner.run_pointers_collecting_extractions(pointers)
    outcome = build_run_outcome(
        recipe.recipe_id,
        extractions,
        recipe_version=recipe.version,
        signals_produced=len(records),
        wall_clock_seconds=wall_clock_seconds,
    )
    return records, outcome


def run_recipe_file(
    path: str | Path,
    fetcher: Fetcher,
    seed_urls: Sequence[str],
    *,
    clock: Clock | None = None,
    llm_extractor: LLMFieldExtractor | None = None,
) -> list[CanonicalRecord]:
    """Same as :func:`run_recipe` but for a recipe loaded from a file path."""
    recipe = load_recipe_file(path)
    runner = RecipeRunner(recipe, fetcher, clock=clock, llm_extractor=llm_extractor)
    return runner.run(seed_urls)


async def persist_dead_letters(
    session: AsyncSession,
    entries: Iterable[DeadLetterEntry],
) -> int:
    """Persist dead-letter entries to the ``recipes_dead_letter`` table.

    Called by the ingestion worker after a run so a field nothing could extract
    is durably recorded — not silently lost (doc 18 §2.3, §3.4). The raw document
    is already in S3 (doc 18 §3.6), so once the recipe is fixed the extraction is
    replayable against the snapshot keyed by ``content_hash``. Returns the number
    of rows staged. The caller owns the transaction (commit).
    """
    count = 0
    for entry in entries:
        session.add(
            DeadLetter(
                recipe_id=entry.recipe_id,
                recipe_version=entry.recipe_version,
                source_url=entry.source_url,
                content_hash=entry.content_hash,
                field_name=entry.field_name,
                reason=entry.reason,
                tried_selectors=list(entry.tried_selectors),
            )
        )
        count += 1
    return count


# ----------------------------------------------------------------------------
# Authoring preview (doc 18 §3, TODO D5)
# ----------------------------------------------------------------------------


def _validate_preview_url(url: str) -> None:
    """Partial SSRF guard: require http/https and reject non-global IP literals.

    **Scope**: This function blocks the most obvious SSRF vectors — non-http(s)
    schemes, empty hostnames (``http:///path``), and IP literals that are not
    globally routable (loopback, link-local, RFC-1918 / ULA private, unspecified
    0.0.0.0/::, multicast, reserved). It does *not* resolve hostnames, so names
    like ``localhost`` or internal DNS entries can still reach private addresses.
    Network-level egress controls (firewall, egress proxy) or a hostname-resolving
    validator are the second line of defense for hostname-based targets.

    Redirect chains are handled by the caller's fetcher; ``HttpxFetcher`` follows
    redirects automatically. A full SSRF fix would disable automatic redirects and
    re-validate each hop, which is deferred to the production connector (D6).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RecipeError("preview URL must use http or https")
    host = parsed.hostname  # None for empty authority (e.g. http:///path)
    if not host:
        raise RecipeError("preview URL must have a non-empty hostname")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return  # not an IP literal — hostname string, allow through
    if not addr.is_global:
        raise RecipeError(
            f"preview URL targets a non-globally-routable address ({host!r}); "
            "loopback, link-local, private, unspecified, multicast, and reserved "
            "addresses are not allowed"
        )


def _resolve_recipe(request: PreviewRequest) -> Recipe:
    """Resolve a :class:`PreviewRequest` to a parsed recipe (id XOR inline YAML)."""
    if (request.recipe_id is None) == (request.recipe_yaml is None):
        raise RecipeError("provide exactly one of recipe_id or recipe_yaml")
    if request.recipe_id is not None:
        return load_recipe(request.recipe_id)
    assert request.recipe_yaml is not None  # narrowed by the XOR check above
    return parse_recipe_yaml(request.recipe_yaml)


def preview_recipe(
    request: PreviewRequest,
    *,
    fetcher: Fetcher | None = None,
) -> PreviewResult:
    """Run a :class:`PreviewRequest` end to end and return a :class:`PreviewResult`.

    Resolves the recipe (by id or inline YAML), validates the one-of input
    constraint (html XOR url), then dry-runs it. URL mode goes through the
    runner's ``fetch`` (robots + politeness honored, doc 18 §2.2) using
    ``fetcher`` — defaulting to an :class:`HttpxFetcher` when none is injected.
    A :class:`RecipeError` is raised for bad requests / posture failures; a
    required-field miss is captured into the result's ``error`` (``ok=False``).
    """
    recipe = _resolve_recipe(request)
    if (request.html is None) == (request.url is None):
        raise RecipeError("provide exactly one of html or url")

    if request.html is not None:
        return preview_html(recipe, request.html)

    assert request.url is not None  # narrowed by the XOR check above
    _validate_preview_url(request.url)
    if fetcher is not None:
        return preview_url(recipe, request.url, fetcher)
    with HttpxFetcher() as live_fetcher:
        return preview_url(recipe, request.url, live_fetcher)


# ----------------------------------------------------------------------------
# Recipe drift: GitHub auto-issue client seam (doc 18 §3.2, TODO E7)
# ----------------------------------------------------------------------------

# Mirrors billing's Stripe-client seam: a process-level client, config-gated to a
# no-op when ``GITHUB_TOKEN`` is unset, overridable in tests. The drift beat task
# resolves the client through :func:`get_github_client` and injects it into
# :func:`evaluate_recipe_drift`, so the LLM/network boundary stays injectable.
_github_client_override: GitHubClient | None = None


def get_github_client() -> GitHubClient:
    """Return the GitHub issue-opener (real when configured, else a no-op).

    No-op when ``GITHUB_TOKEN`` is unset (dev / self-host) — auto-pause still
    happens, no issue is filed (doc 18 §3.2). Tests inject a recording fake via
    :func:`override_github_client`. Not cached across calls so a settings change
    (token added) takes effect; the default client is cheap to construct.
    """
    if _github_client_override is not None:
        return _github_client_override
    settings = _get_settings()
    if not settings.github_token:
        return NoopGitHubClient()
    return HttpxGitHubClient(
        settings.github_token,
        settings.github_repo,
        api_base_url=settings.github_api_base_url,
    )


def override_github_client(client: GitHubClient | None) -> None:
    """Inject a GitHub client (recording fake in tests), or reset with ``None``."""
    global _github_client_override
    _github_client_override = client


__all__ = [
    "WINDOW_7D_HOURS",
    "WINDOW_24H_HOURS",
    "CanonicalRecord",
    "Clock",
    "DeadLetter",
    "DeadLetterEntry",
    "DriftCounters",
    "DriftEvaluation",
    "DriftStateRecord",
    "ExtractedDocument",
    "FetchFailedError",
    "Fetcher",
    "FieldExtraction",
    "FieldPreview",
    "FixtureReplayResult",
    "GatewayFieldExtractor",
    "GitHubClient",
    "HttpxFetcher",
    "HttpxGitHubClient",
    "LLMFieldExtractor",
    "NoopGitHubClient",
    "PreviewRequest",
    "PreviewResult",
    "RealClock",
    "Recipe",
    "RecipeError",
    "RecipeNotFoundError",
    "RecipeRunner",
    "RecipeValidationError",
    "RequiredFieldMissingError",
    "RobotsDisallowedError",
    "RollingMetrics",
    "RunOutcome",
    "SourcePointer",
    "build_run_outcome",
    "clear_drift_state",
    "compute_rolling_metrics",
    "evaluate_recipe_drift",
    "evaluation_subjects",
    "get_drift_state",
    "get_github_client",
    "list_recipe_ids",
    "list_recipe_ids_with_runs",
    "load_recipe",
    "load_recipe_file",
    "make_runner",
    "override_github_client",
    "parse_recipe",
    "parse_recipe_yaml",
    "persist_dead_letters",
    "preview_html",
    "preview_recipe",
    "preview_url",
    "recipes_dir",
    "record_drift_pause",
    "record_run_outcome",
    "render_recipe",
    "replay_fixture",
    "replay_recipe",
    "rollup_metrics",
    "run_pointers",
    "run_pointers_with_outcome",
    "run_recipe",
    "run_recipe_file",
    "scaffold_recipe",
    "validate_recipe_data",
]
