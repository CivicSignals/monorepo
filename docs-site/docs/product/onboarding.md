---
id: onboarding
title: Onboarding walkthrough
sidebar_label: Onboarding
slug: /product/onboarding
---

# Onboarding walkthrough

The onboarding wizard runs automatically after you create a workspace. It lives at `/onboarding` and takes about 10 minutes. You can change everything you set here later from **Settings → ICP**.

The wizard has six steps. A numbered progress indicator at the top shows where you are.

## Step 1 — Geography

Name your ICP (for example, "K-12 West Coast") and choose the countries and US states you want to cover. Leave states blank to receive signals from all US states.

- **ICP name** — a label for this profile. Useful if you create additional ICPs later.
- **Countries** — defaults to United States.
- **States** — a multi-select of US state abbreviations. Filter to your territory here.

## Step 2 — Segments

Choose the public-sector entity types you target. Toggle any combination:

| Entity type | Covers |
|---|---|
| K-12 district | Pre-K through 12th grade public school districts |
| Community college | Two-year public colleges and vocational schools |
| University | Four-year colleges and research universities |
| City | Municipalities, towns, and city governments |
| County | County governments and regional authorities |
| State agency | State-level departments and public agencies |
| Special district | Water, transit, fire, and other special purpose districts |

Leaving all chips unselected means signals from **all** entity types reach your feed.

## Step 3 — Size

Narrow by organisation size so you do not receive signals from entities too large or small for your deal size:

- **Minimum size** — minimum enrollment (for education) or population (for government) in thousands.
- **Maximum size** — optional upper bound.
- **Deal band** — the minimum and maximum estimated deal value in USD that you care about.

Leave these blank to impose no size filter.

## Step 4 — Signal types and weights

Choose which of the twelve signal types matter to you and assign a weight (1–10) to each. Higher-weight signal types contribute more to the ICP score on a signal.

| Signal type | What it indicates |
|---|---|
| RFP posted | A formal request for proposals or invitation for bids has been published. |
| RFI / RFQ | A pre-RFP information-gathering or quote request — early pipeline signal. |
| Contract expiring | An existing contract is within the 6–12 month renewal window. |
| Contract awarded | A contract award has been recorded on a procurement portal or board resolution. |
| Budget approved | A budget document or line item has been approved. |
| Grant awarded | The entity received a grant (potential implementation spend follows). |
| Grant opportunity | A federal or state grant programme is now open. |
| Leadership change | A new CIO, CTO, superintendent, city manager, or equivalent has been appointed. |
| Board agenda item | An upcoming board meeting agenda item touching procurement, budget, or strategy. |
| Strategic plan published | A new multi-year strategic, technology, or capital plan has been published. |
| Open job | A posted role in a procurement-relevant position. |
| News mention | A news article naming the entity in a procurement-relevant context. |

## Step 5 — Keywords and threshold

Refine your feed with keyword filters and a score threshold:

- **Required keywords** — signals must contain at least one of these terms to appear.
- **Excluded keywords** — signals matching any of these terms are suppressed.
- **Score threshold** — the minimum ICP score (0–100) a signal must reach to appear in your feed. The default is 50. Lower it to see more signals; raise it to see only high-confidence matches.

## Step 6 — Review and activate

Review a summary of all your choices. Click **Activate ICP** to save. CivicSignals then:

1. Saves the ICP definition.
2. Marks it as the active ICP for your workspace.
3. Redirects you to the signal feed.

CivicSignals back-fills up to 7 days of signals that match your ICP. If the feed is empty after a few minutes, the empty state suggests ways to broaden your criteria.

## What happens if my ICP is too narrow or too broad?

| Scenario | What CivicSignals does |
|---|---|
| ICP too narrow (empty feed) | Shows specific suggestions: expand geography, add signal types, lower the threshold, or broaden keywords. One-click apply for each suggestion. |
| ICP too broad (>1,000 high-score signals) | The wizard detects this and prompts you to narrow before committing — suggest state filters, entity size bands, or keyword exclusions. |

## Editing your ICP later

After onboarding, go to **Settings → ICP** (or navigate directly to `/icp/<id>/edit`) to modify any parameter. When you save:

- The updated ICP is activated immediately.
- Existing signals are re-scored within 24 hours.
- Saved searches are **not** automatically updated — you must click **Apply ICP to saved searches** explicitly if you want them to reflect the new criteria.

You can also create a second ICP at `/icp/new` and switch between them.
