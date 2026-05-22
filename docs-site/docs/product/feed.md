---
id: feed
title: Signal feed
sidebar_label: Signal feed
slug: /product/feed
---

# Signal feed

<!-- TODO G1 — full feed documentation pending G1 implementation -->

The **signal feed** is the main daily workspace for CivicSignals users: a chronological, scored, and filtered stream of procurement events matched to your ICP.

:::note Coming soon
The signal feed UI (epic G1) is under active development. This page describes the planned behaviour based on the product spec. It will be updated with accurate screenshots and step-by-step instructions when G1 ships.
:::

## What the feed will contain

When the feed ships, it will show:

- **Signals scored above your ICP threshold**, ordered by most recent by default.
- A **filter chip bar** to narrow by signal type, geography, entity type, entity size, date range, and ICP score range.
- Sort options: most recent (default), highest score, oldest.
- **Bulk actions**: assign to user, push to CRM, mark all as seen, dismiss.
- Infinite scroll with 25 signals per page.

## Signal types

CivicSignals tracks twelve canonical signal types:

| Signal type | What it captures |
|---|---|
| **RFP posted** | A formal request for proposals or invitation for bids. |
| **RFI / RFQ** | Pre-RFP information-gathering or quote requests. |
| **Contract expiring** | An existing contract within 6–12 months of its renewal window. |
| **Contract awarded** | A contract award recorded on a portal or board resolution. |
| **Budget approved** | An approved budget document or line item. |
| **Grant awarded** | A grant received by the entity (implementation spend often follows). |
| **Grant opportunity** | A federal or state grant programme now open for applications. |
| **Leadership change** | A new CIO, CTO, superintendent, city manager, or equivalent. |
| **Board agenda item** | An upcoming board meeting agenda item touching procurement or strategy. |
| **Strategic plan published** | A new multi-year strategic, technology, or capital plan. |
| **Open job** | A posted role in a procurement-relevant position. |
| **News mention** | A news article naming the entity in a procurement-relevant context. |

## Signal detail

Each signal will have a detail view with:

- **Source document** — the original PDF or HTML page.
- **Extracted entities** — contacts, budget amounts, due dates, prior vendors.
- **ICP score breakdown** — how the score was calculated.
- **Inspect view** — source document preview, extraction prompt, model version, recipe ID and version, and the extracted JSON. Fully reproducible on demand.
- **"Report this is wrong" button** — flags the signal for recipe-maintainer review.
- **Push to CRM** — sends the signal to Salesforce or HubSpot.
- **Add to pipeline** — creates a pipeline item in the Saved stage.

## Related pages

- [ICP guide](/product/icp) — configure the score threshold and signal weights that filter your feed.
- [Saved searches](/product/saved-searches) — create persistent filters with email or Slack digest delivery.
- [Smart Search](/product/smart-search) — natural-language queries across the signal index.
