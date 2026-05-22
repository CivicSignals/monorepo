---
id: schema
title: Recipe YAML Schema
sidebar_label: Schema reference
slug: /recipes/schema
---

# Recipe YAML Schema

Recipe YAML is validated against the [JSON Schema in `packages/recipe-schema/`](https://github.com/CivicSignals/monorepo/tree/main/packages/recipe-schema). The schema is the **single source of truth** for both the Python runner and the TypeScript tooling — the CLI rejects any recipe that does not pass validation.

## Top-level structure

```yaml
recipe_id: <string>       # required
connector: <string>       # required
version: <integer>        # required, >= 1
entity: { ... }           # required
tenant: { ... }           # optional; platform-connector tenant overrides
schedule: { cron: ... }   # optional
fetch: { ... }            # optional; politeness overrides
prefilter: ...            # optional; classifier | assume_relevant
connector_config: { ... } # optional; connector-specific settings
fields: { ... }           # optional; field extraction map
signal_types: [ ... ]     # optional; expected signal types produced
```

### `recipe_id`

Required. A stable, lowercase identifier for this recipe. Pattern: `[a-z0-9][a-z0-9_-]*`.

Becomes the directory name under `recipes/` and is tagged onto every raw document and signal produced by this recipe.

```yaml
recipe_id: wa-state-webs
```

### `connector`

Required. The id of the connector this recipe instantiates. Must match a known connector id (see [Connectors](./connectors.md)).

```yaml
connector: http_static
```

### `version`

Required. An integer starting at 1. Bump the version when you make a meaningful change to selectors, schedule, or entity mapping. Every raw document and signal produced records the `recipe_version`, so runs are tagged and rollback is possible by changing the version pointer.

```yaml
version: 1
```

### `entity`

Required. Tells the normalizer how to resolve the entity (organization) this recipe produces signals for.

```yaml
entity:
  entity_id: "nces_0010001"    # stable external ID — preferred when available
  name: Washington State Department of Enterprise Services
  state: WA                    # two-letter US state code
  kind: state_agency
```

**`entity_id`** is preferred over name-based resolution when the source publishes a stable external identifier (NCES ID, IPEDS unit ID, FIPS code, UEI). Stable IDs bypass the fuzzy-match / alias-match path and guarantee deduplication.

If you omit `entity_id`, the normalizer tries exact name match, then alias match, then fuzzy match within the same `state`+`kind` scope. If still ambiguous, a new entity row is created with `status = unverified` and queued for human review — so an incorrect name will not silently produce a duplicate entity, but it will create a review queue item.

### `tenant`

Optional. Per-tenant overrides for multi-tenant platform connectors (BoardDocs, Granicus, CivicPlus, etc.). The exact keys depend on the connector.

```yaml
tenant:
  subdomain: "seattleschools"
```

### `schedule`

Optional. Sets the run cadence for the scheduler. Uses standard cron syntax.

```yaml
schedule:
  cron: "0 */2 * * *"   # every 2 hours
```

If omitted, the recipe uses the connector's default schedule. The scheduler adapts the cadence automatically: if a recipe consistently produces zero new signals, the interval is stretched (e.g., 2 hours → 6 hours) to avoid wasting cycles.

### `fetch`

Optional. Overrides the connector's default politeness settings and legal posture.

```yaml
fetch:
  respect_robots_txt: true     # default: true; never set false — rejected in PR review
  politeness_seconds: 10       # minimum delay between requests to the same host (default: 10)
  jitter_seconds: 2            # random extra 0–2s added before each request (default: 0)
  user_agent: "CivicSignalsBot/1.0; +https://civicsignals.io/bot"  # platform default when omitted
  max_redirects: 5             # default: 5; limit to fail visibly on redirect loops
```

`respect_robots_txt: false` is technically valid in the schema (the field exists for auditability) but will be rejected in PR review. See [Politeness and legal rules](./politeness.md).

### `prefilter`

Optional. Controls whether the Stage-2 relevance classifier runs on documents from this recipe.

```yaml
prefilter: assume_relevant    # skip the classifier for pre-vetted sources
prefilter: classifier         # run the classifier (default)
```

Use `assume_relevant` for sources you know always produce relevant signals (e.g., a dedicated state RFP portal). Use the default `classifier` for broader sources (RSS news feeds, general `.gov` pages) where many documents may not produce a signal.

### `connector_config`

Optional. Connector-specific configuration. The key must match the `connector` field exactly — any mismatch is rejected to prevent silent misconfiguration.

See [Connectors](./connectors.md) for the full set of config options per connector.

### `fields`

The field extraction map. Each key is a field name you want to extract; the value describes how to extract it.

```yaml
fields:
  title:
    selectors:
      - "h1.solicitation-title"    # CSS selector (primary)
      - ".bid-header > h2"         # CSS selector (fallback 1)
      - "main h1:first-of-type"    # CSS selector (fallback 2)
    required: true
  due_date:
    attr: datetime                  # read a specific HTML attribute instead of text content
    selectors:
      - ".bid-closing-date time"
      - { selector: "//dt[text()='Due Date']/following-sibling::dd[1]", type: xpath }
    required: false
    llm_assisted: true              # fall back to LLM extraction if all selectors fail
    prompt_name: "due_date_v1"      # optional: named prompt in the registry
```

**Field properties:**

| Property | Type | Default | Description |
|---|---|---|---|
| `selectors` | array | — | Required. Ordered list: index 0 is primary, later entries are fallbacks |
| `attr` | string | — | HTML attribute to read instead of element text (e.g. `datetime`, `href`) |
| `required` | boolean | `false` | If true and no selector matches, the document is dead-lettered |
| `llm_assisted` | boolean | `false` | After all selectors fail, attempt LLM-assisted extraction for this field |
| `prompt_name` | string | — | Named prompt from the registry; falls back to the built-in default |

Each selector is either a plain CSS selector string (shorthand), or an object `{ selector, type }` for XPath selectors:

```yaml
selectors:
  - "td.due-date"                                          # CSS (shorthand)
  - { selector: "//td[@class='due-date']", type: xpath }  # XPath explicit
```

See [Selectors and fallbacks](./selectors.md) for a full explanation of the fallback chain and `degraded` flag.

### `signal_types`

Optional. Documents the signal types this recipe is expected to produce. Used for validation and drift detection.

```yaml
signal_types:
  - rfp_posted
  - contract_award
```

Valid signal types (from the PRD): `rfp_posted`, `rfi_rfq`, `contract_expiring`, `contract_awarded`, `budget_approved`, `grant_awarded`, `grant_opportunity`, `leadership_change`, `board_agenda_item`, `strategic_plan_published`, `open_job`, `news_mention`.

## Full annotated example

The complete `wa-state-webs` recipe with every field explained:

```yaml
# Stable id: lowercase, [a-z0-9_-], matches the recipes/ directory name.
recipe_id: wa-state-webs

# Which connector runs the discover/fetch mechanics.
connector: http_static

# Bump this when you meaningfully change selectors or entity mapping.
version: 1

# Entity resolution. No stable external ID here, so name+state+kind is used.
entity:
  name: Washington State Department of Enterprise Services
  state: WA
  kind: state_agency

# Scheduler cadence: every 2 hours.
schedule:
  cron: "0 */2 * * *"

# Politeness: 10s between requests + up to 2s random jitter, robots.txt honored.
fetch:
  respect_robots_txt: true
  politeness_seconds: 10
  jitter_seconds: 2

# This is a known-good RFP portal: skip the relevance classifier.
prefilter: assume_relevant

# Field extraction: ordered selectors (primary → fallbacks).
fields:
  title:
    selectors:
      - "h1.solicitation-title"
      - ".bid-header > h2"
      - "main h1:first-of-type"
    required: true
  due_date:
    attr: datetime           # read <time datetime="2025-07-15"> not its text content
    selectors:
      - ".bid-closing-date time"
      - "td:contains('Bid Due') + td"
    required: false

# Signal types this recipe produces (for drift detection).
signal_types:
  - rfp_posted
  - contract_award
```

## Schema file location

The canonical schema is at:

```
packages/recipe-schema/schema/recipe.schema.json   # recipe YAML schema
packages/recipe-schema/schema/connector.schema.json # connector descriptor schema
```

The `validate` CLI subcommand runs the recipe through this schema automatically:

```bash
cd apps/api
uv run python -m civicsignals_api.modules.recipes.cli validate recipes/my-recipe/recipe.yml
```
