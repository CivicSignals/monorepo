---
id: cli
title: CLI Reference
sidebar_label: CLI reference
slug: /recipes/cli
---

# Recipe CLI Reference

The recipe authoring CLI is the primary tool for developing, validating, previewing, and testing recipes locally. It lives in the `recipes` module and is invoked via `uv run`:

```bash
cd apps/api
uv run python -m civicsignals_api.modules.recipes.cli <subcommand> [args]
```

The CLI has four subcommands: `validate`, `test`, `preview`, and `scaffold`.

## Prerequisites

You need `uv` and a synced Python environment:

```bash
cd apps/api
uv sync
```

## `validate` — check a recipe against the schema

Validates one or more recipe YAML files against the canonical [JSON Schema](https://github.com/CivicSignals/monorepo/tree/main/packages/recipe-schema). Exits non-zero if any file fails validation.

```bash
uv run python -m civicsignals_api.modules.recipes.cli validate recipes/my-recipe/recipe.yml
```

Multiple files at once:

```bash
uv run python -m civicsignals_api.modules.recipes.cli validate \
  recipes/my-recipe/recipe.yml \
  recipes/another-recipe/recipe.yml
```

Example output (success):

```
OK recipes/wa-state-webs/recipe.yml (recipe_id=wa-state-webs, version=1)
```

Example output (failure):

```
INVALID recipes/my-recipe/recipe.yml
  - 'entity' is a required property
  - 'connector' is a required property
```

**When to use:** after editing `recipe.yml`, before running tests or opening a PR.

## `test` — replay golden fixtures (the CI gate)

Runs every recipe against its committed golden fixtures and compares the extraction output to the `*.expected.json` files. This is the same gate CI runs on every PR.

```bash
# All recipes:
uv run python -m civicsignals_api.modules.recipes.cli test

# One specific recipe:
uv run python -m civicsignals_api.modules.recipes.cli test wa-state-webs

# Multiple specific recipes:
uv run python -m civicsignals_api.modules.recipes.cli test wa-state-webs my-new-recipe
```

Example output (all passing):

```
PASS wa-state-webs :: listing-001

all fixtures passed across 1 recipe(s)
```

Example output (failure with diff):

```
FAIL my-recipe :: listing-001
--- expected
+++ actual
@@ -4,7 +4,7 @@
   "fields": {
-    "title": "My Solicitation",
+    "title": null,
     "due_date": "2025-07-15"

1 fixture(s) failed
```

Exit code is 0 on all-pass, non-zero on any failure.

**When to use:** before opening a PR. Required to pass in CI (task QA-4).

## `preview` — dry-run a recipe against HTML or a URL

Runs `fetch` + `extract` against a local HTML file or a live URL, then prints the extracted fields. Does not write to the database. The live-URL variant honors `robots.txt` and the politeness window from the recipe's `fetch` settings.

### Preview against a local HTML file

```bash
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe wa-state-webs \
  --html recipes/wa-state-webs/fixtures/listing-001.html
```

Or from a recipe file path (before the recipe is committed under `recipes/`):

```bash
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe-file /tmp/my-draft-recipe.yml \
  --html /tmp/sample-page.html
```

### Preview against a live URL

```bash
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe wa-state-webs \
  --url "https://webs.des.wa.gov/solicitations/123"
```

### Output formats

Default human-readable output:

```
OK wa-state-webs v1 <- file:///recipes/wa-state-webs/fixtures/listing-001.html
  signal_types: rfp_posted, contract_award
  fields:
    [x] title (required): 'RFP 2025-014 — District-Wide Network Refresh'
    [x] due_date: '2025-07-15'
```

A `degraded` run shows the flag:

```
OK wa-state-webs v1 <- file:///… degraded
  signal_types: rfp_posted, contract_award
  fields:
    [x] title (required): 'RFP 2025-014 — District-Wide Network Refresh'  ← fallback matched
    [ ] due_date: (no match)
```

A missing required field shows a `MISSING REQUIRED` indicator and exits non-zero:

```
FAILED wa-state-webs v1 <- file:///…
  fields:
    [ ] title (required): (no match)  <- MISSING REQUIRED
```

JSON output (useful for building the `*.expected.json` fixture):

```bash
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe wa-state-webs \
  --html recipes/wa-state-webs/fixtures/listing-001.html \
  --json
```

```json
{
  "recipe_id": "wa-state-webs",
  "recipe_version": 1,
  "source": "file:///…/listing-001.html",
  "ok": true,
  "degraded": false,
  "signal_types": ["rfp_posted", "contract_award"],
  "fields": [
    {
      "name": "title",
      "value": "RFP 2025-014 — District-Wide Network Refresh",
      "matched": true,
      "required": true,
      "missing_required": false
    },
    {
      "name": "due_date",
      "value": "2025-07-15",
      "matched": true,
      "required": false,
      "missing_required": false
    }
  ],
  "error": null
}
```

Exit code is 0 if extraction succeeded (even with `degraded: true`), non-zero on failure or missing required fields.

**When to use:** while developing selectors, to verify each field matches before writing `*.expected.json`.

## `scaffold` — generate a starter recipe skeleton

Creates a `recipes/<id>/recipe.yml` and an empty `fixtures/` directory. The generated YAML is a valid, schema-passing starting point so you never start from a blank file.

```bash
uv run python -m civicsignals_api.modules.recipes.cli scaffold my-new-source
```

With a different connector:

```bash
uv run python -m civicsignals_api.modules.recipes.cli scaffold my-rss-source --connector rss
```

Print to stdout instead of writing a file:

```bash
uv run python -m civicsignals_api.modules.recipes.cli scaffold my-new-source --stdout
```

Overwrite an existing recipe (use carefully):

```bash
uv run python -m civicsignals_api.modules.recipes.cli scaffold my-new-source --force
```

Example generated `recipe.yml`:

```yaml
# Recipe: my-new-source
# Instantiates the `http_static` connector for one source (doc 16 §17.4).
# Edit the entity, selectors, and signal_types below, then validate + preview:
#   python -m civicsignals_api.modules.recipes.cli validate recipes/my-new-source/recipe.yml
#   python -m civicsignals_api.modules.recipes.cli preview --recipe my-new-source --html sample.html
recipe_id: my-new-source
connector: http_static
version: 1

entity:
  name: TODO Source Name
  state: WA
  kind: state_agency

schedule:
  cron: "0 */2 * * *" # every 2 hours

fetch:
  respect_robots_txt: true
  politeness_seconds: 10
  jitter_seconds: 2

prefilter: classifier

fields:
  title:
    selectors:
      - "h1"
    required: true

signal_types:
  - rfp_posted
```

**When to use:** when starting a new recipe. Edit the scaffolded YAML rather than writing from scratch.

## Typical authoring workflow

```bash
cd apps/api

# 1. Generate the skeleton:
uv run python -m civicsignals_api.modules.recipes.cli scaffold my-recipe

# 2. Edit recipes/my-recipe/recipe.yml — update entity, selectors, signal_types.

# 3. Save a sample HTML page from the source:
curl -A "CivicSignalsBot/1.0; +https://civicsignals.io/bot" \
     -o recipes/my-recipe/fixtures/listing-001.html \
     "https://www.example.gov/bids/listing"

# 4. Preview to check selector matches:
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe my-recipe \
  --html recipes/my-recipe/fixtures/listing-001.html

# 5. Generate the expected JSON from the preview output:
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe my-recipe \
  --html recipes/my-recipe/fixtures/listing-001.html \
  --json > recipes/my-recipe/fixtures/listing-001.expected.json

# 6. Validate the recipe YAML:
uv run python -m civicsignals_api.modules.recipes.cli validate recipes/my-recipe/recipe.yml

# 7. Run the fixture replay (CI gate):
uv run python -m civicsignals_api.modules.recipes.cli test my-recipe

# 8. If everything passes, open a PR (see Contributing).
```

## Backward-compatible invocation

Calling the CLI without a subcommand (or with only recipe IDs) runs the fixture replay — the original D1 behavior that CI depends on:

```bash
# Equivalent to `test` with all recipes:
uv run python -m civicsignals_api.modules.recipes.cli

# Equivalent to `test wa-state-webs`:
uv run python -m civicsignals_api.modules.recipes.cli wa-state-webs
```
