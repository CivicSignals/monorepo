---
id: intro
title: Recipe Authoring Guide
sidebar_label: Overview
slug: /recipes/intro
---

# Recipe Authoring Guide

A **recipe** is a declarative YAML file that tells CivicSignals how to ingest data from one specific public-sector source — a city council's meeting portal, a county procurement board, a state grants database, and so on.

Recipes are **community-contributable**: you can add coverage for your jurisdiction by submitting a YAML file via pull request. No code required — just a valid YAML file, the right connector, field selectors, and a set of golden fixtures so CI can verify your recipe.

This guide teaches you everything you need to author and submit a recipe.

## What you'll learn

- [Connectors vs recipes](./connectors.md) — what the difference is and why it matters
- [Recipe YAML schema](./schema.md) — every field, with examples and defaults
- [Choosing a connector](./connectors.md#available-connectors) — `http_static`, `rss`, `rest_api_pager`, `bulk_download`, `pdf_extractor`, and platform connectors
- [Field selectors and fallbacks](./selectors.md) — CSS/XPath ordered chains, the `degraded` flag, and LLM-assisted extraction
- [Golden fixtures](./fixtures.md) — how to write the `*.html` and `*.expected.json` files CI replays
- [Testing locally with the CLI](./cli.md) — `validate`, `test`, `preview`, and `scaffold`
- [Politeness and legal rules](./politeness.md) — the non-negotiable posture every recipe must follow
- [Submitting your PR](./contributing.md) — DCO sign-off, required fixtures, review checklist

## Quick orientation

### Connectors vs recipes

| Concept | What it is | Who writes it |
|---|---|---|
| **Connector** | Code for a *source type* — e.g., all BoardDocs-hosted agendas, all Granicus portals | CivicSignals team |
| **Recipe** | Declarative YAML that instantiates a connector for *one specific source* — e.g., the City of Seattle's BoardDocs portal | Community contributors |

A connector is a code path (Python). A recipe is configuration (YAML). The same `http_static` connector powers hundreds of different state and local procurement pages, each with its own recipe that specifies the right selectors, schedule, and entity.

### The four-stage lifecycle

Every recipe runs through four stages:

```
discover → fetch → extract → normalize
```

1. **discover** — find candidate URLs or records for this run (the index page, the RSS feed, the API paginator)
2. **fetch** — retrieve raw bytes and store them in S3 before any parsing happens
3. **extract** — turn the raw bytes into structured fields using CSS/XPath selectors (with ordered fallbacks)
4. **normalize** — map the extracted fields to canonical records (Signals, Entities, Contacts, etc.)

As a recipe author, you control **discover** (via your connector choice and seed URL), **fetch** (politeness settings), and **extract** (field selectors). The platform handles normalize automatically.

### Extraction fallback chain

The extractor tries each selector in order. The first non-empty match wins:

```
Primary selector (index 0)
  ↓ (on failure)
Fallback selector (index 1)
  ↓ (on failure)
More fallbacks …
  ↓ (all failed + llm_assisted: true)
LLM-assisted extraction via the LLM gateway
  ↓ (failure)
Dead-letter queue + recipe drift alert
```

When a fallback (anything after index 0) produces the match, the document is flagged `degraded: true`. When the LLM-assisted path is used, it is flagged `llm_assisted: true`. The platform counts these events per recipe — a high fallback rate triggers a drift alert prompting a recipe fix.

## Worked example: `wa-state-webs`

The `recipes/wa-state-webs/` directory is the canonical reference recipe. It ingests Washington State's WEBS procurement portal using the `http_static` connector.

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

prefilter: assume_relevant   # state portal is pre-vetted; skip relevance classifier

fields:
  title:
    selectors:
      - "h1.solicitation-title"    # primary
      - ".bid-header > h2"         # fallback 1
      - "main h1:first-of-type"    # fallback 2
    required: true
  due_date:
    attr: datetime                 # read the <time> element's datetime attribute
    selectors:
      - ".bid-closing-date time"
      - "td:contains('Bid Due') + td"
    required: false

signal_types:
  - rfp_posted
  - contract_award
```

Its fixture at `recipes/wa-state-webs/fixtures/listing-001.html` contains a sample HTML page, and `listing-001.expected.json` specifies the exact extraction output CI checks against.

Read the [Worked Example](./example.md) page for the full walkthrough.
