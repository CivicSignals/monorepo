---
id: icp
title: ICP guide
sidebar_label: ICP guide
slug: /product/icp
---

# ICP guide

Your **Ideal Customer Profile (ICP)** is the set of rules CivicSignals uses to score and filter signals before they reach your feed. Getting the ICP right is the single most important configuration step — it determines which signals you see and how they are ranked.

## What an ICP contains

An ICP definition has six dimensions:

| Dimension | Controls |
|---|---|
| **Name** | A label for this ICP, useful when you create multiple profiles. |
| **Geography** | Countries and US states to include. Defaults to all US states. |
| **Entity types (segments)** | Which kinds of public-sector organisations to target. |
| **Entity size** | Minimum and maximum enrollment/population bands and deal-size range. |
| **Signal types and weights** | Which of the twelve signal types to track and how much to weight each. |
| **Keywords and threshold** | Required keywords, excluded keywords, and the minimum score a signal must reach to appear in your feed. |

## Scoring

Each signal receives an **ICP score from 0 to 100**. The score is a weighted combination of:

- How closely the signal's entity type matches your selected segments.
- Whether the entity's geography is in your target states.
- Whether the entity's size falls within your configured bands.
- The signal's type and the weight you assigned to it.
- Keyword matches and exclusions.

Signals scoring below your **threshold** (default: 50) are hidden from the default feed view. They still exist in the database — you can lower the threshold at any time to surface them.

## Creating and editing ICPs

### Creating an ICP

1. First-time: complete the [onboarding wizard](/product/onboarding) at `/onboarding`.
2. Later: go to `/icp/new` to create an additional ICP.

### Editing an ICP

Navigate to `/icp/<id>/edit` or reach it from **Settings → ICP → Edit**. The same six-step wizard is pre-populated with your current values. Save to activate the updated ICP immediately.

### Re-scoring after edits

When you save a changed ICP, CivicSignals schedules a full re-score of all workspace signals. This typically completes within 24 hours. You will receive an email notification when re-scoring finishes.

## Geography

Select one or more US states (two-letter abbreviations). Leave blank to include all states. Signals are matched on the entity's `state` field.

**Tip:** If you sell nationally but have a territory quota, create separate ICPs — one per territory — and switch between them using the workspace switcher or by creating multiple workspaces.

## Entity types (segments)

The seven supported entity types in MVP:

- **K-12 district** — public school districts
- **Community college** — two-year public colleges
- **University** — four-year colleges and research universities
- **City** — municipal governments
- **County** — county and regional governments
- **State agency** — state-level departments
- **Special district** — water, transit, fire, and similar authorities

Leaving all types blank is equivalent to selecting all types.

## Entity size

Size filtering applies to two metrics depending on entity type:

- **Enrollment** — used for K-12 districts, community colleges, and universities.
- **Population** — used for cities, counties, state agencies, and special districts.

Set a **minimum size** to exclude very small entities that are below your deal threshold. Set a **maximum size** to exclude large districts or cities whose procurement processes are dominated by enterprise vendors.

The **deal band** (`deal_band_min_cents` and `deal_band_max_cents`) filters signals whose estimated value falls outside your typical deal range. This field uses the value extracted from the source document when available.

## Signal types and weights

Assign a weight from 1 (low) to 10 (high) to each signal type you care about. The weight multiplies into the ICP score calculation.

**Example weighting for an EdTech SaaS vendor:**

| Signal type | Suggested weight | Rationale |
|---|---|---|
| RFP posted | 10 | Highest purchase intent. |
| Contract expiring | 9 | Competitor renewal window. |
| Budget approved | 7 | Funding confirmed for the year. |
| Leadership change | 6 | New buyer, often re-evaluates vendors. |
| Board agenda item | 5 | Early signal, often precedes RFP. |
| Grant awarded | 4 | Implementation spend typically follows. |
| Strategic plan published | 3 | Long-lead indicator of future spend. |
| News mention | 1 | Broad awareness, not purchase intent. |

Leave a signal type weight at 0 (or unselected) to exclude that type from scoring.

## Keywords

### Required keywords

A signal must match at least one required keyword to appear in your feed. Keywords are matched against the signal's summary and extracted text.

**Example:** `learning management system, LMS, curriculum, student data`

Leave blank to impose no keyword requirement.

### Excluded keywords

Any signal matching an excluded keyword is suppressed from your feed, regardless of its score.

**Example:** `federal, DOD, DARPA` (if you only sell to state/local education)

### Threshold

The minimum ICP score (0–100) a signal must reach to appear in the default feed view. Start at 50 and adjust based on your feed volume:

- **Lower the threshold** if your feed is too sparse — you will see more signals but some will be lower quality.
- **Raise the threshold** if your feed is too noisy — you will see fewer signals but all will be high-confidence matches.

## Multiple ICPs

You can create multiple ICP definitions in a single workspace (for example, "K-12 West" and "Community College National"). Only one ICP is **active** at a time. Activating a new ICP re-scores all signals for that workspace.

:::info v2 roadmap
True multi-ICP per workspace (where multiple ICPs can be active simultaneously and the feed shows the union) is planned for v2.
:::

## Permissions

| Action | Admin | Member | Viewer |
|---|---|---|---|
| View ICP settings | Yes | No | No |
| Create/edit ICP | Yes | No | No |
| Activate a different ICP | Yes | No | No |
