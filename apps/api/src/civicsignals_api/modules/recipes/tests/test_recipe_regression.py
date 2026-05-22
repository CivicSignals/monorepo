"""Recipe regression harness — golden-fixture replay for every recipe (QA-4).

This is the authoritative CI gate for recipe correctness (doc 18 §3.3,
doc 11 §3 CUF-7). It is pytest-parametrized over every
``recipes/<id>/fixtures/*.html`` + ``*.expected.json`` pair discovered at
collection time, so each fixture produces its own test node with a clear,
per-recipe failure message.

## How it works

1. Discovery: :func:`_discover_fixture_pairs` walks ``recipes/`` at collection
   time and returns every ``(recipe_id, <case>.html, <case>.expected.json)``
   tuple.  Any recipe whose ``fixtures/`` directory is absent or empty produces
   a *warning* (not a hard failure) so the harness can run before every recipe
   has fixtures; once a recipe is Tier-1, fixtures are required (doc 18 §3.3).

2. Per-fixture test: ``test_recipe_golden_fixture`` replays one HTML fixture
   through the live runner (same path CI uses, same as the CLI:
   ``python -m civicsignals_api.modules.recipes.cli``) and asserts that the
   projected extraction equals the committed expected JSON.  A mismatch fails
   with a unified diff.

3. Schema validation: ``test_recipe_yaml_valid`` validates every recipe YAML
   against the canonical draft-07 JSON Schema in ``packages/recipe-schema``
   (the single source of truth for both Python and TypeScript — doc 18 §3.1).
   This runs independently of fixture replay, so a malformed recipe fails fast
   without confusing extraction errors.

4. Harness self-test: ``test_harness_detects_mismatch`` creates a temporary
   recipe + mismatched fixture entirely in memory to prove the harness rejects
   drift.  This test does NOT touch ``recipes/`` on disk.

## Adding a new recipe + golden fixtures

1. Create ``recipes/<your-recipe-id>/recipe.yml`` (validate: ``pnpm
   --filter @civicsignals/recipe-schema validate``).
2. Create ``recipes/<your-recipe-id>/fixtures/`` and add at least one
   ``<case>.html`` (a real page captured from the source) and the matching
   ``<case>.expected.json`` (projected extraction shape).
3. Run locally::

       cd apps/api
       uv run pytest src/civicsignals_api/modules/recipes/tests/test_recipe_regression.py -v

4. The harness auto-discovers the new pair on next collection — no code
   changes needed.

See also: ``make check-recipes`` (wraps the CLI replay gate for quick local
iteration) and ``recipes/README.md`` (contributor guide).
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from civicsignals_api.modules.recipes import services
from civicsignals_api.modules.recipes.runner import (
    load_recipe_file,
    recipes_dir,
    validate_recipe_data,
)

# ---------------------------------------------------------------------------
# Fixture-pair discovery (runs at collection time)
# ---------------------------------------------------------------------------

_FixturePair = tuple[str, Path, Path]
"""(recipe_id, html_path, expected_json_path)"""


def _discover_fixture_pairs() -> list[_FixturePair]:
    """Return all (recipe_id, html, expected_json) triples across recipes/.

    Called once at collection time. Recipes with no fixture files emit a
    warning instead of failing so the harness stays green during early recipe
    authoring (doc 18 §3.3: Tier-1 recipes *require* fixtures; lower tiers
    are expected to add them before promotion).
    """
    root = recipes_dir()
    pairs: list[_FixturePair] = []
    for recipe_dir in sorted(root.iterdir()):
        if not (recipe_dir / "recipe.yml").exists():
            continue  # not a recipe directory (e.g. README)
        recipe_id = recipe_dir.name
        fixtures = recipe_dir / "fixtures"
        html_files = sorted(fixtures.glob("*.html")) if fixtures.is_dir() else []
        if not html_files:
            warnings.warn(
                f"recipe {recipe_id!r} has no golden fixtures under {fixtures}; "
                "add at least one <case>.html + <case>.expected.json pair "
                "(doc 18 §3.3). This recipe will be skipped by the regression "
                "harness until fixtures are added.",
                stacklevel=2,
            )
            continue
        for html_path in html_files:
            expected_path = html_path.parent / f"{html_path.stem}.expected.json"
            if not expected_path.exists():
                # Malformed fixture tree — fail loudly, not silently.
                pytest.fail(
                    f"{recipe_id}: fixture HTML {html_path.name!r} has no "
                    f"matching {expected_path.name!r}; add the expected JSON "
                    "or remove the orphaned HTML file."
                )
            pairs.append((recipe_id, html_path, expected_path))
    return pairs


_FIXTURE_PAIRS: list[_FixturePair] = _discover_fixture_pairs()


# ---------------------------------------------------------------------------
# Parametrized per-fixture test
# ---------------------------------------------------------------------------


def _pair_id(triple: _FixturePair) -> str:
    """Human-readable pytest node id: ``wa-state-webs/listing-001``."""
    recipe_id, html_path, _ = triple
    return f"{recipe_id}/{html_path.stem}"


@pytest.mark.parametrize("pair", _FIXTURE_PAIRS, ids=[_pair_id(p) for p in _FIXTURE_PAIRS])
def test_recipe_golden_fixture(pair: _FixturePair) -> None:
    """Replay one HTML fixture and assert it matches the committed expected JSON.

    Failure means a recipe, connector, or prompt change silently changed
    extraction output (doc 18 §3.3). Fix: either repair the recipe/connector
    or regenerate the expected JSON if the new output is intentionally correct.
    """
    recipe_id, html_path, expected_path = pair
    result = services.replay_fixture(
        services.load_recipe(recipe_id),
        html_path,
    )
    assert result.passed, (
        f"{recipe_id}/{html_path.name} extraction diverged from {expected_path.name}.\n"
        f"Re-run `uv run python -m civicsignals_api.modules.recipes.cli {recipe_id}` "
        "locally to inspect the diff, then either fix the recipe or regenerate "
        "the expected JSON if the change is intentional.\n\n"
        f"Diff:\n{result.diff}"
    )


# ---------------------------------------------------------------------------
# Schema validation (independent of fixture replay)
# ---------------------------------------------------------------------------


def _all_recipe_yamls() -> list[Path]:
    root = recipes_dir()
    return sorted(
        recipe_dir / "recipe.yml"
        for recipe_dir in root.iterdir()
        if (recipe_dir / "recipe.yml").exists()
    )


_RECIPE_YAMLS: list[Path] = _all_recipe_yamls()


@pytest.mark.parametrize(
    "recipe_path",
    _RECIPE_YAMLS,
    ids=[p.parent.name for p in _RECIPE_YAMLS],
)
def test_recipe_yaml_valid(recipe_path: Path) -> None:
    """Every recipe YAML must validate against the canonical JSON Schema.

    The schema in ``packages/recipe-schema`` is the single source of truth for
    both Python and TypeScript tooling (doc 18 §3.1). This test runs the same
    validation the runner applies at load time, but as a first-class parametrized
    test so CI reports the failing recipe by name.
    """
    import yaml

    raw = recipe_path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    # validate_recipe_data raises RecipeValidationError on any violation.
    validate_recipe_data(data, recipe_ref=str(recipe_path))


# ---------------------------------------------------------------------------
# Harness self-test: verify the harness catches a deliberate mismatch
# ---------------------------------------------------------------------------


def test_harness_detects_mismatch(tmp_path: Path) -> None:
    """The harness must fail when actual extraction differs from expected JSON.

    Creates a minimal in-memory recipe + fixture pair, deliberately commits a
    *wrong* expected JSON, and asserts the replay flags it as failed (not
    silently passing). This test does NOT touch ``recipes/`` on disk.
    """
    # Write a temporary recipe YAML.
    recipe_yml = tmp_path / "recipe.yml"
    recipe_yml.write_text(
        """
recipe_id: harness-self-test
connector: http_static
version: 1
entity:
  name: Test Agency
  state: WA
  kind: state_agency
fields:
  title:
    selectors:
      - "h1.solicitation-title"
    required: true
signal_types:
  - rfp_posted
""",
        encoding="utf-8",
    )

    # HTML fixture whose h1.solicitation-title is "Actual Title".
    html_path = tmp_path / "case.html"
    html_path.write_text(
        "<html><body><h1 class='solicitation-title'>Actual Title</h1></body></html>",
        encoding="utf-8",
    )

    # Expected JSON that disagrees with the actual extraction ("Wrong Title").
    expected_path = tmp_path / "case.expected.json"
    expected_path.write_text(
        json.dumps(
            {
                "signal_types": ["rfp_posted"],
                "extraction_method": "primary",
                "degraded": False,
                "fields": {"title": "Wrong Title"},  # deliberate mismatch
            }
        ),
        encoding="utf-8",
    )

    recipe = load_recipe_file(recipe_yml)
    result = services.replay_fixture(recipe, html_path)

    assert not result.passed, "harness should have detected the mismatch but returned passed=True"
    assert result.diff is not None, "a mismatch must produce a non-empty unified diff"
    assert "Wrong Title" in result.diff
    assert "Actual Title" in result.diff


def test_harness_passes_on_matching_fixture(tmp_path: Path) -> None:
    """The harness must pass when actual extraction matches the expected JSON.

    Symmetric counterpart of ``test_harness_detects_mismatch``.
    """
    recipe_yml = tmp_path / "recipe.yml"
    recipe_yml.write_text(
        """
recipe_id: harness-pass-test
connector: http_static
version: 1
entity:
  name: Test Agency
  state: WA
  kind: state_agency
fields:
  title:
    selectors:
      - "h1.solicitation-title"
    required: true
  due_date:
    selectors:
      - ".due-date time"
    attr: datetime
    required: false
signal_types:
  - rfp_posted
""",
        encoding="utf-8",
    )

    html_path = tmp_path / "match.html"
    html_path.write_text(
        """
<html><body>
  <h1 class="solicitation-title">RFP 2025-001</h1>
  <span class="due-date"><time datetime="2025-12-01">Dec 1, 2025</time></span>
</body></html>
""",
        encoding="utf-8",
    )

    expected_path = tmp_path / "match.expected.json"
    expected_path.write_text(
        json.dumps(
            {
                "signal_types": ["rfp_posted"],
                "extraction_method": "primary",
                "degraded": False,
                "fields": {"title": "RFP 2025-001", "due_date": "2025-12-01"},
            }
        ),
        encoding="utf-8",
    )

    recipe = load_recipe_file(recipe_yml)
    result = services.replay_fixture(recipe, html_path)

    assert result.passed, f"expected pass but got diff:\n{result.diff}"
    assert result.diff is None


# ---------------------------------------------------------------------------
# Recipes-without-fixtures: discovery warns, not fails
# ---------------------------------------------------------------------------


def test_discovery_warns_on_missing_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recipes without fixture directories emit a warning, not a hard failure.

    This confirms the harness stays green during early recipe authoring and
    reports missing fixtures visibly so contributors know to add them.
    """
    # Create a minimal recipe directory with NO fixtures/ subdirectory.
    recipe_dir = tmp_path / "no-fixtures-recipe"
    recipe_dir.mkdir()
    (recipe_dir / "recipe.yml").write_text(
        """
recipe_id: no-fixtures-recipe
connector: http_static
version: 1
entity:
  name: Test Agency
  state: WA
  kind: state_agency
fields:
  title:
    selectors: ["h1"]
signal_types:
  - rfp_posted
""",
        encoding="utf-8",
    )

    # Redirect recipes_dir to our tmp dir so the discovery runs in isolation.
    monkeypatch.setattr(
        "civicsignals_api.modules.recipes.runner.get_settings",
        lambda: type("S", (), {"recipes_dir": str(tmp_path), "recipe_schema_dir": None})(),
    )

    with pytest.warns(UserWarning, match="no-fixtures-recipe"):
        pairs = _discover_fixture_pairs()

    assert pairs == [], "a recipe without fixtures should produce no parametrize pairs"
