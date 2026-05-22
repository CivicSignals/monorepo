"""Scaffold a starter recipe skeleton (doc 18 §3; TODO D5).

``scaffold`` writes a minimal, schema-valid ``recipe.yml`` (plus an empty
``fixtures/`` dir) under ``recipes/<id>/`` so a contributor starts from a
working recipe instead of a blank file. The generated YAML deliberately mirrors
the example ``wa-state-webs`` recipe's shape and carries inline comments
pointing at the relevant doc sections.

The skeleton is validated against the canonical JSON Schema before it is
written, so a scaffold is never born invalid (if the template ever drifts from
the schema, the scaffold fails loudly instead of emitting a broken file).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .runner import RecipeError, recipes_dir, validate_recipe_data

# A minimal but complete, schema-valid recipe. ``{recipe_id}`` / ``{connector}``
# are substituted in. Required-field guidance is inline so an author knows what
# to edit next (doc 18 §3.4: ordered selectors, primary -> fallbacks).
_TEMPLATE = """\
# Recipe: {recipe_id}
# Instantiates the `{connector}` connector for one source (doc 16 §17.4).
# Edit the entity, selectors, and signal_types below, then validate + preview:
#   python -m civicsignals_api.modules.recipes.cli validate recipes/{recipe_id}/recipe.yml
#   python -m civicsignals_api.modules.recipes.cli preview --recipe {recipe_id} --html sample.html
recipe_id: {recipe_id}
connector: {connector}
version: 1

entity:
  name: TODO Source Name
  state: WA
  kind: state_agency

schedule:
  cron: "0 */2 * * *" # every 2 hours

# Fetch-time politeness + legal posture (doc 18 §2.2, doc 16 §18). Static HTML,
# public listings only; honor robots.txt strictly with the default 10s window.
fetch:
  respect_robots_txt: true
  politeness_seconds: 10
  jitter_seconds: 2

prefilter: classifier

# Ordered selectors: primary -> fallbacks (doc 18 §3.4). The runner records which
# selector matched so a fallback hit can flag `degraded: true`.
fields:
  title:
    selectors:
      - "h1"
    required: true

signal_types:
  - rfp_posted
"""


def render_recipe(recipe_id: str, *, connector: str = "http_static") -> str:
    """Render (and schema-validate) a starter recipe YAML for ``recipe_id``.

    Returns the YAML text without touching the filesystem — handy for the CLI's
    ``--stdout`` mode and for tests. Raises :class:`RecipeError` if ``recipe_id``
    is not a valid id per the schema's pattern, or if the rendered skeleton ever
    fails validation.
    """
    text = _TEMPLATE.format(recipe_id=recipe_id, connector=connector)
    data = yaml.safe_load(text)
    # Defensive: the template should always be valid; surface drift loudly.
    validate_recipe_data(data, recipe_ref=f"scaffold:{recipe_id}")
    return text


def scaffold_recipe(
    recipe_id: str,
    *,
    connector: str = "http_static",
    base_dir: Path | None = None,
    overwrite: bool = False,
) -> Path:
    """Write ``recipes/<recipe_id>/recipe.yml`` (+ ``fixtures/``) and return its path.

    ``base_dir`` defaults to the resolved ``recipes/`` directory; tests pass a
    temp dir. Refuses to clobber an existing recipe unless ``overwrite=True``.
    """
    text = render_recipe(recipe_id, connector=connector)
    root = base_dir if base_dir is not None else recipes_dir()
    recipe_dir = root / recipe_id
    recipe_path = recipe_dir / "recipe.yml"
    if recipe_path.exists() and not overwrite:
        raise RecipeError(f"recipe {recipe_id!r} already exists at {recipe_path}")

    (recipe_dir / "fixtures").mkdir(parents=True, exist_ok=True)
    recipe_path.write_text(text, encoding="utf-8")
    return recipe_path


__all__ = ["render_recipe", "scaffold_recipe"]
