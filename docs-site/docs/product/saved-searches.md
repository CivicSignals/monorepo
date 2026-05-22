---
id: saved-searches
title: Saved searches
sidebar_label: Saved searches
slug: /product/saved-searches
---

# Saved searches

<!-- TODO H1 — full saved-search documentation pending H1 implementation -->

**Saved searches** let you name and persist a set of signal filters so they run continuously, appear in your sidebar, and optionally deliver results via email digest or Slack notification.

:::note Coming soon
The saved-search creation UI (epic H) is not yet built. This page describes the planned feature based on the product spec. It will be updated with accurate UI instructions when the feature ships.
:::

## What a saved search is

A saved search is a named filter set that the system evaluates against every new signal as it arrives. When a signal matches, it is added to that saved search's result set. You can subscribe to a saved search with an email digest (daily or weekly) and/or a Slack channel destination.

## Planned filters

Saved searches will support the same filters as the signal feed:

| Filter | Values |
|---|---|
| Geography | US state, region, or nationwide |
| Entity type | K-12 district, city, county, university, community college, state agency, special district |
| Entity size | Population / enrollment bands |
| Signal type | Any of the twelve signal types |
| Signal date range | Signals extracted in the last N days |
| ICP score range | Minimum and maximum score |
| Keyword | Free-text keyword match |

## Planned limits

| Plan | Saved searches per workspace |
|---|---|
| Solo | 20 |
| Starter | 50 |
| Pro | 200 |
| Enterprise / Self-hosted | Unlimited |

## Email digest

Each saved search can have an optional email digest delivered to the subscriber:

- **Off** — no digest.
- **Daily** — a summary of new signals since yesterday, sent each morning.
- **Weekly** — a weekly recap of all new signals, sent on a day you choose.

## Slack delivery

When a Slack integration is configured in **Settings → Integrations**, each saved search can post new matching signals to a chosen Slack channel in real time.

## Related pages

- [ICP guide](/product/icp) — configure the global ICP that drives the default feed.
- [Signal feed](/product/feed) — the main chronological feed view.
- [Integrations](/product/integrations) — connect Slack to enable channel delivery.
