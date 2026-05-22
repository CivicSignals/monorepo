---
id: smart-search
title: Smart Search
sidebar_label: Smart Search
slug: /product/smart-search
---

# Smart Search

<!-- TODO I3 — full smart-search documentation pending I3 implementation -->

**Smart Search** lets you query the signal index in plain English. Instead of selecting filter dropdowns, you type a natural-language question and CivicSignals converts it to a structured filter and returns a ranked result set.

:::note Coming soon
The Smart Search UI landing (epic I3) is under development. This page describes the planned feature. It will be updated when the feature ships.
:::

## Example queries

> "California community colleges considering AI procurement expiring 2026"

> "Cities over 50,000 population in Texas that approved a technology budget in the last 90 days"

> "K-12 districts in the southeast that posted an RFP for student information systems in the last 6 months"

## How it works (planned)

1. You type a natural-language query in the Smart Search box.
2. The LLM converts the query to a structured filter (geography, entity type, signal type, date range, keywords).
3. CivicSignals runs the structured filter against the signal index and returns ranked results.
4. The structured filter is shown below the query box — you can edit it to fine-tune results.
5. Click **Save as search** to turn the result into a persistent saved search.

## Rate limits

Smart Search is rate-limited per plan:

| Plan | Smart searches per day |
|---|---|
| Solo | 20 |
| Starter | 100 |
| Pro | 1,000 |
| Enterprise / Self-hosted | Unlimited |

Current usage appears in **Settings → Billing → Usage**.

## Slack slash command

When Slack is connected, the `/civic search <query>` slash command runs a Smart Search and posts the top results to the channel where the command was issued. Rate limits apply per workspace across all channels.

## Related pages

- [Signal feed](/product/feed) — the default scored and filtered feed.
- [Saved searches](/product/saved-searches) — persist a Smart Search result as a saved filter.
- [ICP guide](/product/icp) — configure the ICP score threshold used to pre-rank Smart Search results.
