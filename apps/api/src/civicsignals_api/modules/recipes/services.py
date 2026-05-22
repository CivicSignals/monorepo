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

from collections.abc import Sequence
from pathlib import Path

from .fixtures import (
    list_recipe_ids,
    replay_fixture,
    replay_recipe,
)
from .runner import (
    Clock,
    Fetcher,
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
    ExtractedDocument,
    FixtureReplayResult,
    Recipe,
    SourcePointer,
)


def make_runner(
    recipe: Recipe,
    fetcher: Fetcher,
    *,
    clock: Clock | None = None,
) -> RecipeRunner:
    """Construct a :class:`RecipeRunner` for an already-parsed recipe."""
    return RecipeRunner(recipe, fetcher, clock=clock)


def run_recipe(
    recipe_id: str,
    fetcher: Fetcher,
    seed_urls: Sequence[str],
    *,
    clock: Clock | None = None,
) -> list[CanonicalRecord]:
    """Load recipe ``recipe_id`` and run its full lifecycle over ``seed_urls``."""
    recipe = load_recipe(recipe_id)
    runner = RecipeRunner(recipe, fetcher, clock=clock)
    return runner.run(seed_urls)


def run_recipe_file(
    path: str | Path,
    fetcher: Fetcher,
    seed_urls: Sequence[str],
    *,
    clock: Clock | None = None,
) -> list[CanonicalRecord]:
    """Same as :func:`run_recipe` but for a recipe loaded from a file path."""
    recipe = load_recipe_file(path)
    runner = RecipeRunner(recipe, fetcher, clock=clock)
    return runner.run(seed_urls)


__all__ = [
    "CanonicalRecord",
    "Clock",
    "ExtractedDocument",
    "Fetcher",
    "FixtureReplayResult",
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
    "replay_fixture",
    "replay_recipe",
    "run_recipe",
    "run_recipe_file",
    "validate_recipe_data",
]
