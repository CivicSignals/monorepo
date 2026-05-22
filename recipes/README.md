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

## The 200-recipe Tier-1 sprint (TODO D12)

The Tier-1 wedge target is **200 recipes** (doc 16 §14, doc 15 §3 cold-start),
broken down as:

| Category (doc 16 §14) | Target | Examples in this repo |
|---|---|---|
| Platform mega-recipes (#1–10) | ~10 | `boarddocs-k12-meetings`, `granicus-peak-muni-meetings`, `granicus-legistar-meetings`, `civicplus-muni-meetings`, `civicclerk-muni-meetings`, `socrata-contracts-template`, `ckan-grants-template`, `arcgis-capital-projects-template`, `usaspending-sled-awards`, `gdelt-sled-news-template` |
| State portals (#11–22) | ~50 | `wa-des-webs2`, `or-orpin-solicitations`, `ca-cal-eprocure`, `tx-smartbuy-esbd`, `fl-vendor-bid-system`, `il-bidbuy-procurement`, `ca-grants-portal` |
| K-12 / community college (#23–60, #101–115) | ~70 | `seattle-ps-boarddocs`, `portland-ps-boarddocs`, `lausd-boarddocs`, `sd-unified-granicus`, `tacoma-ps-boarddocs`, `maricopa-ccd-boarddocs`, `foothill-deanza-ccd-bids` |
| Cities / counties / news / staff directories (#61–100, #116–170) | ~70 | `austin-tx-granicus`, `houston-tx-legistar`, `miamidade-fl-legistar`, `chicago-il-legistar`, `govtech-news-rss`, `statescoop-news-rss`, `route-fifty-news-rss`, `wa-k12-staff-directory` |

### What is shipped vs. what remains

This directory currently holds a **representative batch spanning all four
categories** (the 15 wave-1/2/3 connector examples plus the D12 batch above) —
**not** all 200. The full 200 is a content sprint requiring live-source access
to author + verify selectors against each tenant's real HTML/JSON (doc 15 §4:
"~1–2 hours per recipe with AI assist"). Every recipe here uses **realistic but
synthetic** fixtures so the QA-4 harness exercises extraction **without hitting
live services**.

The remaining recipes toward 200 are added as **community/operator YAML PRs**
(the recipe model, doc 16 §17.4): no code change, just a new `recipes/<id>/`
directory that the path-filtered `recipe-fixtures` CI job validates + replays
automatically. Each new recipe should reference its row in doc 16 §14/§20.

### The "platform mega-recipe" pattern (highest leverage)

Most of the 200 are **clones of a platform mega-recipe**, not net-new authoring.
A mega-recipe (e.g. `boarddocs-k12-meetings`) carries the platform-wide selector
chain (primary → fallbacks, doc 18 §3.4); a per-tenant recipe is the same file
with `entity` + `connector_config` swapped and the selectors reused verbatim.
The fallbacks absorb per-tenant theming, so one pattern covers thousands of
tenants. To add a tenant:

1. **Scaffold or clone.** Either start from the mega-recipe directory:
   ```bash
   cp -r recipes/boarddocs-k12-meetings recipes/<new-tenant-id>
   # then edit entity + connector_config; reuse the selectors
   ```
   …or scaffold a fresh skeleton for a non-platform source:
   ```bash
   cd apps/api
   uv run python -m civicsignals_api.modules.recipes.cli scaffold <new-id> --connector <connector>
   ```
2. **Capture one synthetic (or live-snapshot) fixture** under `fixtures/`.
3. **Generate the expected JSON** with the runner (see the snippet in "Adding a
   new recipe", or just run `cli test <new-id>` after writing it by hand).
4. **Validate + replay** locally, then open the PR.

> The synthetic fixtures here intentionally exercise the *primary* selector
> chain; `boarddocs-k12-meetings/meeting-002-fallback` additionally exercises
> the **fallback** rung (`extraction_method: fallback`, `degraded: true`) so the
> harness proves the ordered-fallback path stays wired (doc 18 §3.4).
