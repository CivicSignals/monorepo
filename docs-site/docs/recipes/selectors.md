---
id: selectors
title: Selectors and Fallbacks
sidebar_label: Selectors & fallbacks
slug: /recipes/selectors
---

# Selectors and Fallbacks

Selectors are the heart of a recipe. They tell the extractor where to find each data field in the raw HTML (or XML, or JSON). Getting selectors right — and providing good fallbacks — is what makes a recipe resilient when the source's page layout changes.

## Selector types

### CSS selectors (default)

The most common type. Use standard CSS selector syntax. The runner extracts the text content of the matched element (or the attribute specified by `attr`).

```yaml
fields:
  title:
    selectors:
      - "h1.solicitation-title"      # element with class
      - "#main-content > h1"         # ID + child combinator
      - "main h1:first-of-type"      # pseudo-class
```

A plain string in the `selectors` array is always treated as CSS. To be explicit:

```yaml
selectors:
  - { selector: "h1.solicitation-title", type: css }
```

### XPath selectors

Use XPath when CSS is not expressive enough — for example, selecting an element based on the text content of a sibling.

```yaml
fields:
  due_date:
    selectors:
      - ".bid-closing-date time"     # CSS primary
      - { selector: "//dt[text()='Due Date']/following-sibling::dd[1]", type: xpath }
```

XPath requires `lxml` (included in the `ingestion` extra). XPath and CSS selectors can be mixed freely in the same `selectors` list.

### Reading an attribute instead of text content

Use `attr` when the value you want is in an HTML attribute, not the element's text.

```yaml
fields:
  due_date:
    attr: datetime     # read <time datetime="2025-07-15">July 15, 2025</time>
                       # → "2025-07-15", not "July 15, 2025"
    selectors:
      - ".bid-closing-date time"
  
  source_url:
    attr: href         # read the href of an anchor element
    selectors:
      - "a.rfp-link"
```

`attr` applies to all selectors in the list.

## The ordered fallback chain

The `selectors` list is ordered: **index 0 is the primary selector; every subsequent entry is a fallback**.

```yaml
fields:
  title:
    selectors:
      - "h1.solicitation-title"   # primary (index 0)
      - ".bid-header > h2"        # fallback 1 (index 1)
      - "main h1:first-of-type"   # fallback 2 (index 2)
    required: true
```

The runner tries each selector in order. The first one that returns a non-empty result wins. If a **fallback** (index ≥ 1) is the one that succeeds, the document is flagged `degraded: true`. This tells the platform that the primary selector no longer matches and the recipe probably needs updating.

### Why `degraded: true` matters

A degraded flag is not a failure — the signal still gets produced. But:

- The platform tracks the per-recipe **degraded rate** (rolling 7-day window).
- If the LLM fallback rate exceeds 20% over 7 days, a drift alert is filed.
- Repeated fallback usage is the signal that the source has changed its HTML structure and the recipe needs a primary-selector update.

This gives you a safety net: your recipe keeps working even when the source makes minor layout tweaks, and you get an alert before the fallback itself breaks.

### LLM-assisted extraction

Add `llm_assisted: true` to a field to enable LLM extraction as the last resort before dead-lettering:

```yaml
fields:
  title:
    selectors:
      - "h1.solicitation-title"
      - ".bid-header > h2"
    required: true
    llm_assisted: true    # if both selectors fail, ask the LLM gateway
```

When all CSS/XPath selectors fail and `llm_assisted: true` is set, the runner sends the raw document text to the LLM gateway with a standard extraction prompt. If the LLM succeeds, the field is extracted and the document is flagged `degraded: true`.

LLM extraction costs approximately $0.005 per document (Haiku-class model). Use it sparingly — only on `required` fields where a missing value would dead-letter important signals. Do not set `llm_assisted: true` on optional fields; it is more expensive than it's worth for fields that can be absent.

To use a named prompt (for reproducibility and version tracking):

```yaml
    llm_assisted: true
    prompt_name: "due_date_extraction_v1"
```

### Dead-letter

If all selectors fail *and* LLM extraction either is not enabled or also fails:

- **Required fields:** the document is dead-lettered. It goes to the dead-letter queue and a drift alert is filed if this happens repeatedly.
- **Optional fields:** extraction continues without the field. The document is not dead-lettered.

## The full fallback sequence (diagram)

```
                ┌──────────────────────────┐
                │   Primary selector       │
                │   (index 0)              │
                └────────────┬─────────────┘
                             │ non-empty match → done
                             │ empty
                             ▼
                ┌──────────────────────────┐
                │   Fallback selectors     │
                │   (index 1, 2, …)        │
                └────────────┬─────────────┘
                             │ non-empty match → done, degraded: true
                             │ all empty
                             ▼
                ┌──────────────────────────┐
                │   LLM-assisted           │  ← only if llm_assisted: true
                │   extraction             │
                └────────────┬─────────────┘
                             │ success → done, degraded: true
                             │ failure (or not enabled)
                             ▼
           ┌─────────────────────────────────────┐
           │   required: true  → dead-letter     │
           │   required: false → field omitted   │
           └─────────────────────────────────────┘
```

## Practical tips

### Writing robust CSS selectors

Prefer **semantic class names** over positional selectors:

```yaml
# Good — semantic and stable
- "h1.solicitation-title"
- ".bid-closing-date time"

# Fragile — breaks when the page adds a column
- "table tr:nth-child(2) td:nth-child(3)"
```

When the source uses meaningful IDs, prefer them:

```yaml
- "#rfp-title"
- "#due-date-field"
```

### Providing good fallbacks

Write fallbacks that match the *same data* via a different structural path. Common patterns:

```yaml
# Semantic class → generic heading
- "h1.solicitation-title"
- "main h1"

# Specific attribute → broader text pattern
- ".bid-closing-date time"            # machine-readable datetime attribute
- "td:contains('Bid Due') + td"       # next sibling of labeled cell

# Exact class → positional fallback in a known container
- ".rfp-header > h2.title"
- "#rfp-details h2:first-child"
```

### Testing selectors

Use the `preview` CLI subcommand to test your selectors against a real HTML file without committing anything:

```bash
cd apps/api
# Test against a saved local HTML file:
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe-file recipes/my-recipe/recipe.yml \
  --html sample-page.html

# Test against a live URL (honors robots.txt + politeness):
uv run python -m civicsignals_api.modules.recipes.cli preview \
  --recipe-file recipes/my-recipe/recipe.yml \
  --url "https://www.example.gov/bids/123"
```

The output shows which selectors matched, what they extracted, and whether the document would be flagged `degraded`. See [CLI reference](./cli.md) for full details.
