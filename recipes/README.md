# recipes/

Declarative, schema-validated **recipes** — the per-tenant/per-source
instantiations of connectors (doc 16 §17.4, doc 18 §3). Connectors are code we
write (`apps/api` ingestion module); recipes are config that community
contributors can submit via PR (no code, no marketplace UI in MVP).

## Layout

```
recipes/
  <recipe_id>/
    recipe.yml          # validated against @civicsignals/recipe-schema
    fixtures/           # 1–5 sample inputs (HTML/JSON/PDF) — doc 18 §3.3
      <case>.html
      <case>.expected.json
```

## CI contract (QA-4)

On every PR, the `recipe-fixtures` CI job runs two gates:

1. **JSON Schema validation** — every `recipe.yml` is validated against
   `packages/recipe-schema/schema/recipe.schema.json` (the shared
   TypeScript/Python source of truth) via `check-jsonschema`.
2. **Golden-fixture regression harness** — a pytest-parametrized suite
   (`apps/api/.../recipes/tests/test_recipe_regression.py`) replays every
   `fixtures/*.html` through the live runner and diffs the output against
   `*.expected.json`.  Each fixture is a separate pytest node so the CI log
   names the failing recipe explicitly.

Adding a fixture is how you fix "the recipe missed this case in production" —
commit the HTML snapshot and the expected extraction JSON and they are
immediately picked up by the harness on the next run.

## Running locally

```bash
# Quick CLI replay (all recipes):
cd apps/api
uv run python -m civicsignals_api.modules.recipes.cli

# One recipe:
uv run python -m civicsignals_api.modules.recipes.cli wa-state-webs

# Parametrized pytest (one node per fixture pair, verbose diff on failure):
uv run pytest src/civicsignals_api/modules/recipes/tests/test_recipe_regression.py -v

# Or via the Makefile target (if you have `make` available):
make check-recipes
```

## Adding a new recipe + golden fixtures

1. **Create the recipe directory:**
   ```
   recipes/<your-recipe-id>/
     recipe.yml
     fixtures/
       <case>.html        # HTML snapshot captured from the live source
       <case>.expected.json
   ```

2. **Write `recipe.yml`** — follow the shape of `wa-state-webs/recipe.yml`.
   Required top-level keys: `recipe_id`, `connector`, `version`, `entity`,
   `fields`, `signal_types`.  Validate against the schema:
   ```bash
   uvx --from "check-jsonschema==0.37.2" check-jsonschema \
     --schemafile packages/recipe-schema/schema/recipe.schema.json \
     recipes/<your-recipe-id>/recipe.yml
   ```

3. **Capture a fixture** — save a representative HTML page from the source
   as `fixtures/<case>.html`.  Use multiple fixture files (up to 5) to cover
   edge cases (empty fields, fallback selectors, different date formats, etc.).

4. **Generate the expected JSON** — run the CLI against your fixture and
   redirect the output, then review and commit it:
   ```bash
   cd apps/api
   # The CLI prints PASS/FAIL per fixture and exits non-zero on drift.
   # To generate the expected JSON for a brand-new fixture, run the runner
   # directly and capture the projected output:
   uv run python - <<'EOF'
   from civicsignals_api.modules.recipes import services
   from pathlib import Path
   import json

   recipe = services.load_recipe("your-recipe-id")
   result = services.replay_fixture(recipe, Path("../recipes/your-recipe-id/fixtures/case.html"))
   print(json.dumps(result.actual, indent=2, sort_keys=True))
   EOF
   ```
   Pipe that output to `recipes/<your-recipe-id>/fixtures/<case>.expected.json`.

5. **Run the harness locally** to confirm green:
   ```bash
   uv run pytest src/civicsignals_api/modules/recipes/tests/test_recipe_regression.py \
     -k your-recipe-id -v
   ```

6. **Open a PR** — the `recipe-fixtures` CI job runs automatically on
   `recipes/**` changes and will gate merge on green fixtures.

> **Tier-1 recipes require at least one golden fixture** (doc 18 §3.3).
> The harness emits a `UserWarning` for recipes without fixtures and skips
> them (no test node generated) so the CI stays green during early authoring,
> but the warning is visible in the CI log.

The 200-recipe Tier-1 sprint (TODO D12) populates this directory.
