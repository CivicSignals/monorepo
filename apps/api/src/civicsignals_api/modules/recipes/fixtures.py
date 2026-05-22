"""Golden-fixture replay for recipes (doc 18 §3.3; TODO D1, D5, QA-4).

Every recipe ships 1-5 sample inputs under ``recipes/<id>/fixtures/`` as
``<case>.html`` + ``<case>.expected.json``. This module runs a recipe's
``extract`` over each fixture and compares the result to the committed expected
JSON. CI replays these on every PR so a recipe author (or a connector / prompt
change) can't silently break extraction.

The expected-JSON shape is the projection of an :class:`ExtractedDocument` that
matters for regression: ``signal_types`` (the recipe's declared list),
``extraction_method``, ``degraded``, ``fields``.
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path

from .runner import Recipe, RecipeError, RecipeRunner, load_recipe, recipes_dir
from .schemas import ExtractedDocument, ExtractionMethod, FixtureReplayResult


def _project(doc: ExtractedDocument) -> dict[str, object]:
    """Project an extraction to the committed expected-JSON shape.

    ``extraction_method`` is an :class:`ExtractionMethod` enum on the model; we
    project its string value so the committed ``*.expected.json`` (which stores a
    plain string like ``"primary"`` / ``"fallback"``) compares cleanly.
    """
    method = doc.extraction_method
    return {
        "signal_types": list(doc.signal_types),
        "extraction_method": method.value if isinstance(method, ExtractionMethod) else method,
        "degraded": doc.degraded,
        "fields": dict(doc.fields),
    }


def _canonical(obj: object) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


def replay_fixture(recipe: Recipe, html_path: Path) -> FixtureReplayResult:
    """Replay one fixture and compare to its ``*.expected.json`` sibling."""
    # `case.html` -> `case.expected.json`
    expected_path = html_path.parent / f"{html_path.stem}.expected.json"
    if not expected_path.exists():
        raise RecipeError(f"no expected JSON next to fixture {html_path}")

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")

    runner = RecipeRunner(recipe, _NoopFetcher())
    extracted = runner.extract_html(html, source_url=f"fixture://{html_path.name}")
    actual = _project(extracted)

    passed = actual == expected
    diff: str | None = None
    if not passed:
        diff = "\n".join(
            difflib.unified_diff(
                _canonical(expected).splitlines(),
                _canonical(actual).splitlines(),
                fromfile=f"{expected_path.name} (expected)",
                tofile="actual",
                lineterm="",
            )
        )

    return FixtureReplayResult(
        recipe_id=recipe.recipe_id,
        fixture=html_path.name,
        passed=passed,
        expected=expected,
        actual=actual,
        diff=diff,
    )


def replay_recipe(recipe_id: str) -> list[FixtureReplayResult]:
    """Replay every ``*.html`` fixture for a recipe id."""
    recipe = load_recipe(recipe_id)
    fixtures_dir = recipes_dir() / recipe_id / "fixtures"
    html_files = sorted(fixtures_dir.glob("*.html"))
    if not html_files:
        raise RecipeError(f"recipe {recipe_id!r} has no .html fixtures in {fixtures_dir}")
    return [replay_fixture(recipe, html_path) for html_path in html_files]


def list_recipe_ids() -> list[str]:
    """All recipe ids under ``recipes/`` (dirs containing a ``recipe.yml``)."""
    root = recipes_dir()
    return sorted(p.name for p in root.iterdir() if (p / "recipe.yml").exists())


class _NoopFetcher:
    """A fetcher that never makes a network call — fixture replay only touches
    ``extract``, so this just satisfies the :class:`RecipeRunner` constructor."""

    def fetch(
        self, url: str, *, user_agent: str, max_redirects: int
    ) -> tuple[int, str, dict[str, str]]:  # pragma: no cover - never called
        raise RecipeError("fixture replay must not fetch over the network")

    def robots_txt(
        self, url: str, *, user_agent: str
    ) -> str | None:  # pragma: no cover - never called
        return None
