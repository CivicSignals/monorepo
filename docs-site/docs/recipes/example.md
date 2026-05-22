---
id: example
title: "Worked Example: wa-state-webs"
sidebar_label: "Worked example"
slug: /recipes/example
---

# Worked Example: `wa-state-webs`

This page walks through the `wa-state-webs` recipe end to end — from inspecting the source page to writing the fixture and getting CI to pass. It is the canonical reference for recipe authoring.

The source is the [Washington State WEBS procurement portal](https://webs.des.wa.gov), which publishes RFP solicitations as static HTML pages. The recipe uses the `http_static` connector.

## 1. Inspect the source

Before writing a recipe, understand the source:

- What entity does this source represent? (Washington State Department of Enterprise Services)
- What connector type fits? The pages are static HTML — no JavaScript required to see the content — so `http_static` is correct.
- What data do we want? Solicitation title and due date.
- How often should we run? State portals post new solicitations throughout the week; every 2 hours is appropriate.
- What does `robots.txt` say? Allows general crawling; no path restrictions on `/bids/`.

## 2. Scaffold the recipe

```bash
cd apps/api
uv run python -m civicsignals_api.modules.recipes.cli scaffold wa-state-webs
```

This creates:

```
recipes/wa-state-webs/
  recipe.yml      # starter YAML, edit this
  fixtures/       # empty, add fixtures here
```

## 3. Edit the recipe YAML

Open `recipes/wa-state-webs/recipe.yml` and fill in the details:

```yaml title="recipes/wa-state-webs/recipe.yml"
recipe_id: wa-state-webs
connector: http_static
version: 1

entity:
  name: Washington State Department of Enterprise Services
  state: WA
  kind: state_agency

schedule:
  cron: "0 */2 * * *"   # every 2 hours

fetch:
  respect_robots_txt: true
  politeness_seconds: 10
  jitter_seconds: 2

# State portal — all documents are relevant to procurement signals
prefilter: assume_relevant

fields:
  title:
    selectors:
      - "h1.solicitation-title"    # primary: semantic class, most specific
      - ".bid-header > h2"         # fallback 1: parent container + generic heading
      - "main h1:first-of-type"    # fallback 2: main content + first h1
    required: true

  due_date:
    # The machine-readable date is in the <time> element's datetime attribute:
    # <time datetime="2025-07-15">July 15, 2025</time>
    # We want "2025-07-15", not "July 15, 2025", so we read the attr.
    attr: datetime
    selectors:
      - ".bid-closing-date time"
      - "td:contains('Bid Due') + td"
    required: false                # due date can be missing; extraction continues

signal_types:
  - rfp_posted
  - contract_award
```

### Key decisions in this recipe

**Three selectors for `title`, one for `due_date`:** The title is required, so we invest in multiple fallbacks. The due date is optional, so two selectors (primary + one fallback) is enough.

**`attr: datetime` for due_date:** The visible text "July 15, 2025" is not machine-readable. The `<time datetime="2025-07-15">` element has the ISO date in its attribute. We read the attribute directly rather than parsing the display text.

**`prefilter: assume_relevant`:** This source is a dedicated RFP portal. Every document is a solicitation. Skipping the relevance classifier saves compute.

**`jitter_seconds: 2`:** Small random delay prevents lockstep requests when multiple state-portal recipes run on the same schedule.

## 4. Save a fixture HTML file

Capture a representative page from the source:

```bash
curl -A "CivicSignalsBot/1.0; +https://civicsignals.io/bot" \
     -o recipes/wa-state-webs/fixtures/listing-001.html \
     "https://webs.des.wa.gov/solicitations/2025-014"
```

For the purposes of this example, the fixture HTML is:

```html title="recipes/wa-state-webs/fixtures/listing-001.html"
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

## 5. Preview the extraction

```bash
cd apps/api
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe wa-state-webs \
  --html recipes/wa-state-webs/fixtures/listing-001.html
```

Output:

```
OK wa-state-webs v1 <- file:///recipes/wa-state-webs/fixtures/listing-001.html
  signal_types: rfp_posted, contract_award
  fields:
    [x] title (required): 'RFP 2025-014 — District-Wide Network Refresh'
    [x] due_date: '2025-07-15'
```

Both fields matched with the primary selector — no degraded flag. The `due_date` value is `2025-07-15` (from the `datetime` attribute), not `July 15, 2025` (from the element text).

## 6. Generate the expected JSON

```bash
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe wa-state-webs \
  --html recipes/wa-state-webs/fixtures/listing-001.html \
  --json > recipes/wa-state-webs/fixtures/listing-001.expected.json
```

The resulting `listing-001.expected.json`:

```json title="recipes/wa-state-webs/fixtures/listing-001.expected.json"
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

## 7. Validate and run the CI gate

```bash
# Schema validation:
uv run python -m civicsignals_api.modules.recipes.cli validate recipes/wa-state-webs/recipe.yml
# OK recipes/wa-state-webs/recipe.yml (recipe_id=wa-state-webs, version=1)

# Fixture replay:
uv run python -m civicsignals_api.modules.recipes.cli test wa-state-webs
# PASS wa-state-webs :: listing-001
# all fixtures passed across 1 recipe(s)
```

Both pass. The recipe is ready to submit.

## 8. What CI checks on every PR

When you open a PR, CI automatically:

1. Validates `recipe.yml` against the JSON Schema.
2. Runs `test wa-state-webs` — replays every fixture pair and diffs the output against the expected JSON.
3. Fails the PR if any fixture drifts from its expected output.

This guarantees that your recipe is always in a runnable, verified state.

## Adding a fallback fixture

Let's say the WEBS portal sometimes uses a slightly different layout for older solicitations:

```html title="recipes/wa-state-webs/fixtures/listing-002.html (fallback scenario)"
<!doctype html>
<html lang="en">
  <body>
    <main>
      <!-- Old layout: h2 in .bid-header instead of h1.solicitation-title -->
      <div class="bid-header">
        <h2>RFP 2024-099 — Legacy Procurement</h2>
      </div>
      <table>
        <tr>
          <td>Bid Due</td>
          <td>2024-12-01</td>
        </tr>
      </table>
    </main>
  </body>
</html>
```

Preview it:

```bash
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe wa-state-webs \
  --html recipes/wa-state-webs/fixtures/listing-002.html
```

```
OK wa-state-webs v1 <- file:///… degraded
  signal_types: rfp_posted, contract_award
  fields:
    [x] title (required): 'RFP 2024-099 — Legacy Procurement'    ← fallback matched
    [x] due_date: '2024-12-01'
```

The `title` matched via `.bid-header > h2` (fallback 1) and the `due_date` matched via `td:contains('Bid Due') + td` (fallback selector). The document is flagged `degraded: true`.

Generate the expected JSON for this fixture:

```json title="recipes/wa-state-webs/fixtures/listing-002.expected.json"
{
  "signal_types": ["rfp_posted", "contract_award"],
  "extraction_method": "fallback",
  "degraded": true,
  "fields": {
    "title": "RFP 2024-099 — Legacy Procurement",
    "due_date": "2024-12-01"
  }
}
```

Now `test wa-state-webs` covers both the primary and fallback paths.

## Bumping the recipe version

When the source's HTML structure changes and you need to update selectors:

1. Update the selectors in `recipe.yml`.
2. Bump `version: 1` → `version: 2`.
3. Update the `*.expected.json` fixtures to reflect the new extraction output.
4. Run `cli test wa-state-webs` to verify.
5. Commit and open a PR.

The version tag means every raw document and signal produced by the old recipe is tagged `recipe_version: 1` and every document produced after the update is tagged `recipe_version: 2`. This makes it easy to identify when a schema change affected extraction, and to reprocess historical documents with the new selectors if needed.
