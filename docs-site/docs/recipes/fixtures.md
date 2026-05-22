---
id: fixtures
title: Golden Fixtures
sidebar_label: Golden fixtures
slug: /recipes/fixtures
---

# Golden Fixtures

Every recipe must ship with at least one **golden fixture**: a sample input paired with the expected extraction output. CI replays all fixtures on every PR and rejects any that produce different output than what you committed. This is the recipe regression harness (task QA-4).

## Why fixtures are required

Fixtures protect three things:

1. **Your recipe** — if someone else changes the connector code, CI catches regressions against your expected output.
2. **Other recipes** — if you accidentally break an existing recipe while adding yours, CI catches it.
3. **The platform** — if a connector code change or LLM prompt change silently alters extraction output, CI catches it.

Adding a fixture is also how you fix "the recipe missed this case in production." When you encounter a new page shape in the wild, save the HTML and add it as a fixture so CI can cover that case from then on.

## File layout

```
recipes/
  <recipe_id>/
    recipe.yml
    fixtures/
      <case>.html          # sample HTML input
      <case>.expected.json # expected extraction output
```

Each fixture is a pair of files with matching names (before the extension). You can have up to five fixture pairs per recipe.

```
fixtures/
  listing-001.html
  listing-001.expected.json
  listing-002.html           # alternative page shape
  listing-002.expected.json
```

## Capturing a fixture HTML file

1. Visit the source page in a browser.
2. Save the page as HTML-only (not "complete" with assets — just the raw HTML document).
3. Clean up personally identifying information if any appears in the document (unlikely for public procurement pages, but worth checking).
4. Name it `<case>.html` — use descriptive names: `listing-001.html`, `detail-rfp.html`, `empty-listing.html`.

Alternatively, use `curl` to save the raw response:

```bash
curl -A "CivicSignalsBot/1.0; +https://civicsignals.io/bot" \
     -o fixtures/listing-001.html \
     "https://www.example.gov/bids/listing"
```

## Writing the expected JSON

The expected JSON must match the structure the runner produces. Run `preview` against your HTML file first to see the actual output:

```bash
cd apps/api
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe-file recipes/my-recipe/recipe.yml \
  --html recipes/my-recipe/fixtures/listing-001.html \
  --json
```

The `--json` flag prints the full result as JSON. Use that output as the starting point for your `*.expected.json`.

### Expected JSON structure

```json
{
  "signal_types": ["rfp_posted", "contract_award"],
  "extraction_method": "primary",
  "degraded": false,
  "fields": {
    "title": "RFP 2025-014 — District-Wide Network Refresh",
    "due_date": "2025-07-15"
  }
}
```

**Fields:**

| Field | Type | Description |
|---|---|---|
| `signal_types` | array of strings | The `signal_types` from the recipe (not from extraction; carried forward) |
| `extraction_method` | string | `"primary"`, `"fallback"`, or `"llm_assisted"` |
| `degraded` | boolean | `true` if any field used a fallback or LLM-assisted extraction |
| `fields` | object | The extracted field values, keyed by field name |

### `wa-state-webs` example

The reference fixture pair:

`recipes/wa-state-webs/fixtures/listing-001.html`:

```html
<!doctype html>
<html lang="en">
  <head><title>WEBS — Solicitation</title></head>
  <body>
    <main>
      <header class="bid-header">
        <h1 class="solicitation-title">RFP 2025-014 — District-Wide Network Refresh</h1>
      </header>
      <dl class="bid-meta">
        <dt>Bid Closing Date</dt>
        <dd class="bid-closing-date"><time datetime="2025-07-15">July 15, 2025</time></dd>
      </dl>
    </main>
  </body>
</html>
```

`recipes/wa-state-webs/fixtures/listing-001.expected.json`:

```json
{
  "signal_types": ["rfp_posted", "contract_award"],
  "extraction_method": "primary",
  "degraded": false,
  "fields": {
    "title": "RFP 2025-014 — District-Wide Network Refresh",
    "due_date": "2025-07-15"
  }
}
```

## Testing fixtures locally

Run the same command CI runs:

```bash
cd apps/api

# Replay all fixtures for all recipes:
uv run python -m civicsignals_api.modules.recipes.cli test

# Replay fixtures for one recipe:
uv run python -m civicsignals_api.modules.recipes.cli test wa-state-webs
```

Output:

```
PASS wa-state-webs :: listing-001

all fixtures passed across 1 recipe(s)
```

A failure shows a diff between the expected and actual output, making it easy to identify what changed:

```
FAIL my-recipe :: listing-001
--- expected
+++ actual
@@ -4,7 +4,7 @@
   "fields": {
-    "title": "RFP 2025-014 — District-Wide Network Refresh",
+    "title": null,
     "due_date": "2025-07-15"
   }

1 fixture(s) failed
```

Exit code is non-zero on any failure, so `cli test` can be used directly as a CI gate.

## Fixture authoring checklist

Before opening a PR, verify:

- [ ] At least one fixture pair per recipe (`<case>.html` + `<case>.expected.json`)
- [ ] The HTML file is the actual page content the recipe's connector would see (not a browser-rendered full-page save with all assets)
- [ ] `cli test <recipe_id>` passes locally
- [ ] If the recipe uses fallback selectors, add a fixture that exercises the fallback path (with `"degraded": true` in the expected JSON)
- [ ] No PII or sensitive data in the fixture HTML

## Adding fixtures for new production cases

When a recipe misses a case in production (for example, a source occasionally uses a different page template for large procurements), the fix is:

1. Save the failing HTML as a new fixture file.
2. Run `preview` against it to see what the current recipe extracts.
3. Update the recipe selectors if needed, or add `llm_assisted: true` for the relevant field.
4. Generate the correct expected JSON and commit both.
5. Run `cli test` to verify all fixtures pass.
6. Open a PR with the new fixture and any selector changes.

Fixtures grow over time and become the recipe's regression suite. A recipe with five fixtures covering five different page variants is significantly more robust than one with a single fixture.
