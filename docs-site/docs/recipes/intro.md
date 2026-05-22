---
id: intro
title: Recipe Authoring Guide
sidebar_label: Overview
slug: /recipes/intro
---

# Recipe Authoring Guide

A **recipe** is a declarative YAML file that tells CivicSignals how to ingest data from one specific public-sector source (a city council's meeting portal, a county procurement board, a state grants database, etc.).

Recipes are community-contributable — you can add coverage for your jurisdiction by submitting a YAML file via pull request.

<!-- TODO Q5: Replace this stub with the full recipe authoring guide. See task Q5 in TODO.md.
  Planned content:
  - Recipe YAML schema reference (sourced from packages/recipe-schema/)
  - Connector types (http_static, rss, rest_api_pager, boarddocs, granicus_peak, etc.)
  - Selector syntax (CSS, XPath, JSONPath) + ordered fallback chain
  - Writing golden fixtures (*.html + *.expected.json in recipes/<id>/fixtures/)
  - Testing your recipe locally (CLI tool from D5)
  - Submitting a recipe PR (DCO sign-off, fixture requirements)
  - Recipe lifecycle: discover → fetch → extract → normalize
  Requires Q5 dependency tasks: D5 (recipe authoring tooling + CLI).
-->

## What's coming (Q5)

Full recipe authoring documentation is planned for **Q5 — Recipe authoring guide**, after the CLI tooling (task **D5**) is built.

## Concepts

### Connectors vs recipes

| Concept | What it is |
|---|---|
| **Connector** | Code for a *source type* — e.g., "BoardDocs-hosted agendas", "Granicus meeting portals". Written by the CivicSignals team. |
| **Recipe** | Declarative YAML that instantiates a connector for *one specific source* — e.g., the City of Seattle's BoardDocs portal. Community-contributable. |

### Recipe lifecycle

```
discover → fetch → extract → normalize
```

Each stage can fall back gracefully:

1. **Primary selectors** (CSS/XPath/JSONPath) — fast, deterministic
2. **Fallback selectors** — alternative paths for when source layout changes
3. **LLM-assisted extraction** — Sonnet-class model for ambiguous content
4. **Dead-letter** — flagged for human review when all fallbacks fail

Recipes that use fallback paths are tagged `degraded: true` in their output.

## Example recipe

```yaml
# recipes/wa-state-webs/recipe.yaml
id: wa-state-webs
connector: http_static
entity_id: c_wa_dol
schedule: "0 6 * * *"
url: "https://www.dol.wa.gov/business/webs/"
selectors:
  - type: css
    path: "table.webs-table tbody tr"
    fields:
      title: "td:nth-child(1)"
      due_date: "td:nth-child(3)"
      value: "td:nth-child(4)"
```

## Schema reference

Recipe YAML is validated against the JSON Schema in [`packages/recipe-schema/`](https://github.com/CivicSignals/monorepo/tree/main/packages/recipe-schema). This schema is the single source of truth for both the Python runner and TypeScript tooling.

Full schema documentation and authoring walkthrough will be added in **Q5**.
