"""CLI to replay recipe golden fixtures (doc 18 §3.3; TODO D1, D5, QA-4).

Run all recipes::

    uv run python -m civicsignals_api.modules.recipes.cli

Run one recipe::

    uv run python -m civicsignals_api.modules.recipes.cli wa-state-webs

Exit code is non-zero if any fixture's extraction diverges from its committed
``*.expected.json`` — wire this into CI to catch recipe/connector/prompt drift.
The richer authoring UX (live preview in the staff UI) is TODO D5; this is the
deterministic CI gate.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from .fixtures import list_recipe_ids, replay_recipe
from .runner import RecipeError
from .schemas import FixtureReplayResult


def _run(recipe_ids: Sequence[str]) -> int:
    ids = list(recipe_ids) if recipe_ids else list_recipe_ids()
    if not ids:
        print("no recipes found under recipes/", file=sys.stderr)
        return 1

    failures = 0
    for recipe_id in ids:
        try:
            results = replay_recipe(recipe_id)
        except RecipeError as exc:
            print(f"FAIL {recipe_id}: {exc}", file=sys.stderr)
            failures += 1
            continue
        for result in results:
            failures += _report(result)
    if failures:
        print(f"\n{failures} fixture(s) failed", file=sys.stderr)
        return 1
    print(f"\nall fixtures passed across {len(ids)} recipe(s)")
    return 0


def _report(result: FixtureReplayResult) -> int:
    if result.passed:
        print(f"PASS {result.recipe_id} :: {result.fixture}")
        return 0
    print(f"FAIL {result.recipe_id} :: {result.fixture}", file=sys.stderr)
    if result.diff:
        print(result.diff, file=sys.stderr)
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return _run(args)


if __name__ == "__main__":  # pragma: no cover - entrypoint
    raise SystemExit(main())
