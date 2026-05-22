---
id: connectors
title: Choosing a Connector
sidebar_label: Connectors
slug: /recipes/connectors
---

# Choosing a Connector

Every recipe names exactly one `connector`. The connector is the code path that handles `discover`, `fetch`, and the low-level mechanics of reading from the source. You configure it — you do not write it.

## Connectors vs recipes (design rationale)

Writing a **connector** is engineering work: one connector per source *type*, code-reviewed, tested, maintained by the CivicSignals team. There are roughly 25–30 connectors total.

Writing a **recipe** is configuration work: one recipe per source *instance*, declarative YAML, no code required, community-contributable. There are hundreds to thousands of recipes.

The ratio is intentional. One `boarddocs` connector powers every K-12 district that uses BoardDocs — potentially thousands of recipe instances — from a single, well-tested code path.

## Available connectors

### `http_static`

Generic HTTP fetcher for static HTML pages. Handles retries, exponential backoff, conditional GET (`If-None-Match` / `If-Modified-Since`), robots.txt, and the politeness window.

Use when: the source is a crawlable HTML page whose content is present in the server-rendered HTML (no JavaScript required to see the data).

```yaml
connector: http_static
connector_config:
  http_static:
    timeout_seconds: 30       # default
    max_attempts: 3           # total fetch attempts on transient 5xx / network errors
    backoff_seconds: 1        # base for exponential backoff between retries
    conditional_get: true     # use If-None-Match / If-Modified-Since (default: true)
```

All fields are optional; the defaults shown above apply when you omit `connector_config` entirely for this connector.

**Examples:** State procurement portals, district board-minute pages, `.gov` agency sites with stable HTML structure.

---

### `rss`

RSS/Atom feed reader. `discover()` parses the feed and emits one `SourcePointer` per new item. Optionally fetches each item's linked article body.

Use when: the source publishes an RSS or Atom feed.

```yaml
connector: rss
connector_config:
  rss:
    feed_url: "https://www.govtech.com/rss.xml"   # defaults to the recipe's seed URL when omitted
    max_items: 50                                  # cap items per run (newest first)
    fetch_item_body: true                          # fetch the full linked article (default: true)
```

**Examples:** GovTech, StateScoop, EdScoop, Route Fifty, governor press release feeds.

---

### `rest_api_pager`

REST/JSON client with pagination, authentication, and rate limiting. `discover()` walks pages of results since the last run.

Use when: the source exposes a documented JSON API.

```yaml
connector: rest_api_pager
connector_config:
  rest_api_pager:
    base_url: "https://api.grants.gov/v1/api/search2"
    pagination:
      mode: offset              # cursor | offset | page
      items_path: oppHits       # dotted path to the array in each response
      offset_param: offset      # query param name
      limit_param: rows
      page_size: 25
      max_pages: 100            # safety cap
    auth:
      mode: none                # none | api_key | bearer
    rate_limit_per_minute: 60
```

For sources that require an API key, use `env:` references — never commit plaintext secrets:

```yaml
    auth:
      mode: api_key
      header: X-Api-Key
      value: "env:GRANTS_GOV_API_KEY"   # resolved from environment at run time
```

**Examples:** Grants.gov, USAspending.gov, OpenStates, Urban Institute Education Data API.

---

### `bulk_download`

Periodic file fetch for CSV/JSON/zip dumps. `discover()` checks whether the file has changed (via ETag, `Last-Modified`, or content hash) before downloading.

Use when: the source publishes periodic data dumps rather than a queryable API.

```yaml
connector: bulk_download
connector_config:
  bulk_download:
    file_url: "https://nces.ed.gov/ccd/Data/zip/ccd_sch_029_2324_w_1a_091724.zip"
    format: zip                       # csv | json | zip (inferred from URL/Content-Type if omitted)
    checkpoint_strategy: etag         # etag | last_modified | content_hash
```

**Examples:** NCES Common Core of Data, IPEDS, Census of Governments, NAICS codes, FIPS codes.

---

### `pdf_extractor`

PDF text extraction chain: pdfplumber text extraction → OCR fallback (for image-only PDFs) → optional LLM table extraction. Not scheduled directly — consumed by other connectors when a fetched URL resolves to a PDF.

```yaml
connector: pdf_extractor
connector_config:
  pdf_extractor:
    ocr_fallback: true         # run OCR when no text layer (default: true)
    llm_table_fallback: false  # use LLM gateway for table extraction (default: false)
    max_pages: 50              # cap pages extracted for large PDFs
```

**Examples:** District budget PDFs, board meeting minutes in PDF format, capital improvement plans.

---

### Platform connectors

Platform connectors cover multi-tenant SaaS systems where one connector reaches thousands of government entities. Each recipe instantiates the connector for a specific tenant via a `tenant` block.

| Connector | Platform | Coverage |
|---|---|---|
| `boarddocs` | BoardDocs (Diligent) | ~10,000 K-12 districts + community colleges |
| `granicus_peak` | Granicus PEAK / Legistar | ~7,000 government orgs |
| `civicplus` | CivicPlus / CivicClerk | ~2,500 municipalities |
| `socrata` | Socrata SODA API | Hundreds of state/city open-data portals |
| `ckan` | CKAN | Hundreds more (Data.gov, state catalogs) |
| `arcgis_rest` | ArcGIS REST | GIS/tabular government data |

Example (`boarddocs` tenant recipe):

```yaml
connector: boarddocs
tenant:
  subdomain: "seattleschools"   # tenant-specific override
```

Platform connectors are not yet all implemented; see the project roadmap for current status. When a platform connector is not yet available, you may be able to use `http_static` against the platform's public HTML if its structure is stable.

## Connector selection guide

| Source type | Use this connector |
|---|---|
| Static HTML page (no JS required) | `http_static` |
| RSS or Atom feed | `rss` |
| REST/JSON API with pagination | `rest_api_pager` |
| Periodic CSV/JSON/zip download | `bulk_download` |
| PDF document | `pdf_extractor` |
| BoardDocs portal | `boarddocs` |
| Granicus meeting portal | `granicus_peak` |
| CivicPlus/CivicClerk site | `civicplus` |
| Socrata open-data portal | `socrata` |
| CKAN catalog | `ckan` |

If a source requires JavaScript execution to render its content, it may require the `http_browser` connector (Playwright-based). This connector is not yet in the wave-1 build and is only added when at least three recipes genuinely require it. If you believe your source needs headless browsing, open an issue rather than a recipe PR.

## What connectors do not cover

The following sources are explicitly out of scope:

- **LinkedIn** — legal exposure, ToS hostile. Use `.gov`/`.edu` staff directories instead.
- **Commercial aggregators** (BidNet, DemandStar, etc.) — ToS-restricted; use the state's primary portal.
- **Sources requiring CAPTCHA bypass** — never; non-negotiable (see [Politeness and legal rules](./politeness.md)).
- **Sources requiring residential proxy rotation** — same reason.
- **Login-gated content** — only the public-side equivalent is ingested.
