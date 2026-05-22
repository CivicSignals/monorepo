#!/usr/bin/env python3
"""Golden-fixture replay harness for recipes (doc 18 §3.3).

For every recipe under ``recipes/<id>/`` this script replays each
``fixtures/<case>.html`` (or ``.json`` / ``.pdf``) through the recipe runner and
diffs the produced extraction against the committed ``<case>.expected.json``.

The recipe runner itself (the connector ``extract`` chain) lands with TODO D1;
until it is merged there is nothing to run, so this harness *degrades
gracefully*: it discovers the fixtures, checks that every ``*.expected.json``
has a matching input file (and vice-versa), and exits 0 with a clear message.
Once D1 ships a runner CLI, wire it into ``_run_recipe`` below and the diff
assertion turns on automatically.

    # TODO D1/QA-4: import the recipe runner and replace _run_recipe's stub with
    # a real extract() call, then flip RUNNER_AVAILABLE.

Usage:
    python scripts/replay_fixtures.py [RECIPES_DIR]
Exit codes:
    0  all fixtures consistent (and, when a runner exists, all replays matched)
    1  a fixture is malformed, orphaned, or a replay mismatched
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Flipped to True by TODO D1 once a recipe runner CLI is importable here.
RUNNER_AVAILABLE = False

INPUT_SUFFIXES = (".html", ".json", ".pdf", ".xml", ".csv")


def _expected_files(fixtures_dir: Path) -> list[Path]:
    return sorted(fixtures_dir.glob("*.expected.json"))


def _input_for(expected: Path) -> Path | None:
    """The raw input that pairs with ``<case>.expected.json`` is ``<case>.*``."""
    case = expected.name[: -len(".expected.json")]
    for suffix in INPUT_SUFFIXES:
        candidate = expected.with_name(f"{case}{suffix}")
        if candidate.exists():
            return candidate
    return None


def _run_recipe(recipe_dir: Path, input_file: Path) -> dict[str, object]:
    """Replay one fixture through the recipe runner.

    TODO D1/QA-4: replace this stub with a real call into the connector
    ``extract`` chain (e.g. ``from civicsignals_api.modules.ingestion.services
    import run_recipe_against``) so the returned dict can be diffed against the
    committed expectation.
    """
    raise NotImplementedError("recipe runner not available yet (TODO D1)")


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path("recipes")
    if not root.is_dir():
        print(f"no recipes directory at {root}; nothing to replay")
        return 0

    recipe_dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / "recipe.yml").exists())
    if not recipe_dirs:
        print(f"no recipes found under {root}")
        return 0

    errors: list[str] = []
    replayed = 0
    checked = 0

    for recipe_dir in recipe_dirs:
        fixtures_dir = recipe_dir / "fixtures"
        if not fixtures_dir.is_dir():
            print(f"- {recipe_dir.name}: no fixtures/ directory (allowed)")
            continue

        expected_files = _expected_files(fixtures_dir)
        # Every input must have an expectation and vice-versa.
        inputs = {
            p
            for p in fixtures_dir.iterdir()
            if p.suffix in INPUT_SUFFIXES and not p.name.endswith(".expected.json")
        }
        paired_inputs: set[Path] = set()

        for expected in expected_files:
            checked += 1
            # The committed expectation must itself be valid JSON.
            try:
                expected_doc = json.loads(expected.read_text())
            except json.JSONDecodeError as exc:
                errors.append(f"{expected}: invalid JSON ({exc})")
                continue

            input_file = _input_for(expected)
            if input_file is None:
                errors.append(f"{expected}: no matching input fixture (expected <case>.html/.json/...)")
                continue
            paired_inputs.add(input_file)

            if RUNNER_AVAILABLE:
                produced = _run_recipe(recipe_dir, input_file)
                if produced != expected_doc:
                    errors.append(
                        f"{recipe_dir.name}/{input_file.name}: replay output does not "
                        f"match {expected.name}"
                    )
                else:
                    replayed += 1

        orphaned = inputs - paired_inputs
        for orphan in sorted(orphaned):
            errors.append(f"{orphan}: input fixture has no matching .expected.json")

        print(f"- {recipe_dir.name}: {len(expected_files)} fixture(s) discovered")

    if errors:
        print("\nFixture validation errors:", file=sys.stderr)
        for err in errors:
            print(f"  {err}", file=sys.stderr)
        return 1

    if RUNNER_AVAILABLE:
        print(f"\nReplayed {replayed} fixture(s); all matched expectations.")
    else:
        print(
            f"\nChecked {checked} fixture pairing(s). Recipe runner not available "
            "yet (TODO D1/QA-4) — skipping output replay; fixtures are consistent."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
