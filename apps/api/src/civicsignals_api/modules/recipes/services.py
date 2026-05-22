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

from collections.abc import Iterable, Sequence
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from .fixtures import (
    list_recipe_ids,
    replay_fixture,
    replay_recipe,
)
from .http_fetcher import HttpxFetcher
from .models import DeadLetter
from .preview import preview_html, preview_url
from .runner import (
    Clock,
    FetchFailedError,
    Fetcher,
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
    ExtractedDocument,
    FieldExtraction,
    FieldPreview,
    FixtureReplayResult,
    PreviewRequest,
    PreviewResult,
    Recipe,
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
    if fetcher is not None:
        return preview_url(recipe, request.url, fetcher)
    with HttpxFetcher() as live_fetcher:
        return preview_url(recipe, request.url, live_fetcher)


__all__ = [
    "CanonicalRecord",
    "Clock",
    "DeadLetter",
    "DeadLetterEntry",
    "DriftCounters",
    "ExtractedDocument",
    "FetchFailedError",
    "Fetcher",
    "FieldExtraction",
    "FieldPreview",
    "FixtureReplayResult",
    "GatewayFieldExtractor",
    "HttpxFetcher",
    "LLMFieldExtractor",
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
    "SourcePointer",
    "list_recipe_ids",
    "load_recipe",
    "load_recipe_file",
    "make_runner",
    "parse_recipe",
    "parse_recipe_yaml",
    "persist_dead_letters",
    "preview_html",
    "preview_recipe",
    "preview_url",
    "recipes_dir",
    "render_recipe",
    "replay_fixture",
    "replay_recipe",
    "run_recipe",
    "run_recipe_file",
    "scaffold_recipe",
    "validate_recipe_data",
]
