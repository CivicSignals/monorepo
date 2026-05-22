"""Public service interface for the recipes module.

Other modules call recipes only through the functions defined here — never by
importing the recipes module's models or routes directly (doc 06 §3).

This module owns the **recipe DSL** and the **recipe runner** (doc 18 §2-§3,
TODO D1): loading + JSON-Schema-validating recipe YAML, and driving the
``discover -> fetch -> extract -> normalize`` lifecycle through an injectable
fetcher. The ingestion module's Celery tasks (TODO D4/D6) call into here rather
than reaching into the runner internals.
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
from .models import DeadLetter
from .runner import (
    Clock,
    Fetcher,
    GatewayFieldExtractor,
    LLMFieldExtractor,
    RealClock,
    RecipeError,
    RecipeRunner,
    RecipeValidationError,
    RequiredFieldMissingError,
    RobotsDisallowedError,
    load_recipe,
    load_recipe_file,
    parse_recipe,
    validate_recipe_data,
)
from .schemas import (
    CanonicalRecord,
    DeadLetterEntry,
    DriftCounters,
    ExtractedDocument,
    FieldExtraction,
    FixtureReplayResult,
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


__all__ = [
    "CanonicalRecord",
    "Clock",
    "DeadLetter",
    "DeadLetterEntry",
    "DriftCounters",
    "ExtractedDocument",
    "Fetcher",
    "FieldExtraction",
    "FixtureReplayResult",
    "GatewayFieldExtractor",
    "LLMFieldExtractor",
    "RealClock",
    "Recipe",
    "RecipeError",
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
    "persist_dead_letters",
    "replay_fixture",
    "replay_recipe",
    "run_pointers",
    "run_recipe",
    "run_recipe_file",
    "validate_recipe_data",
]
