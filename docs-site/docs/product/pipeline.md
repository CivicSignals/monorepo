---
id: pipeline
title: Pipeline
sidebar_label: Pipeline
slug: /product/pipeline
---

# Pipeline

The **pipeline** is a lightweight opportunity tracker built into CivicSignals. It is designed as a usable default for teams without a CRM, and as a staging area before pushing signals to Salesforce or HubSpot when a CRM is connected.

The pipeline report lives at `/pipeline`.

## Stages

Every workspace starts with nine default stages that mirror common SLED (State, Local, and Education) sales workflows:

| Stage | What it means |
|---|---|
| **Saved** | You have bookmarked this signal for later review. |
| **Researching** | You are investigating the entity or signal before reaching out. |
| **Contacted** | You have sent initial outreach. |
| **Meeting Booked** | A discovery call or demo is scheduled. |
| **Qualified** | You have confirmed budget, authority, need, and timing. |
| **Proposal / RFP** | You are preparing or have submitted a proposal or RFP response. |
| **Won** | The deal closed in your favour. |
| **Lost** | The deal was awarded to a competitor or cancelled. |
| **Disqualified** | The opportunity was not a fit and you are not pursuing it. |

Admins can add, rename, reorder, or delete stages (a stage can only be deleted when it has no items). The stage list is workspace-scoped — all members see the same stages.

## Pipeline items

A **pipeline item** tracks one opportunity. Each item has:

| Field | Description |
|---|---|
| **Title** | A short name for the opportunity (often pre-filled from the signal title). |
| **Stage** | The current Kanban column. |
| **Notes** | Free-text notes visible to the item owner. |
| **Value estimate** | Optional deal value in USD. Shown in the pipeline report. |
| **Status** | A finer-grained status within the stage: `active`, `won`, `lost`, `disqualified`. |
| **Owner** | The team member responsible for this opportunity. |
| **Signal link** | The originating CivicSignals signal (if the item was created from one). |

### Moving items

Items can be moved between stages at any time. When an item moves to **Won**, **Lost**, or **Disqualified**, the status field updates automatically to reflect the outcome.

## Activity timeline

Every pipeline item has an **activity timeline** that records:

- Stage transitions (from stage → to stage, timestamp, actor)
- Comments added by team members
- Assignment changes
- Value estimate changes
- Integration pushes (when a signal is pushed to a CRM)

The timeline is chronological (oldest first) and cursor-paginated. Use it to review the full history of an opportunity.

### Adding comments

Open the item detail and use the comment input to add a free-text note. Comments appear in the timeline alongside automatic events.

## Pipeline report

The pipeline report at `/pipeline` shows a workspace-level rollup:

- **Bar chart** — item counts per stage, scaled to the highest-count stage.
- **Summary table** — per-stage item count and total estimated value.
- **Totals** — total items across all stages and total pipeline value.

The report updates in real time as items are added or moved.

:::info Admin view
Admins can view the full team pipeline including items owned by any member. Members see only their own items by default. The admin team rollup (per-rep statistics, per-saved-search pipeline conversion) is planned for a future release.
:::

## CRM integration

When a Salesforce or HubSpot connection is configured in **Settings → Integrations**, pushing a signal to the CRM from a pipeline item creates or upserts the corresponding Account and Lead/Contact in the CRM. Stage transitions in CivicSignals do not automatically sync back to the CRM — CivicSignals is push-only in MVP (two-way sync is planned for v2).

## Permissions

| Action | Admin | Member | Viewer |
|---|---|---|---|
| View own pipeline items | Yes | Yes | No |
| Create/update pipeline items | Yes | Yes | No |
| View all team pipeline items | Yes | No | No |
| Add/delete stages | Yes | No | No |
| View pipeline report | Yes | Yes | No |
