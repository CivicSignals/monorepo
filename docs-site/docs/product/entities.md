---
id: entities
title: Entities and contacts
sidebar_label: Entities and contacts
slug: /product/entities
---

# Entities and contacts

The **entity directory** is the master list of public-sector organisations CivicSignals tracks. Every signal is attached to an entity. Browse the directory at `/entities`.

## Entity directory

The directory lists school districts, cities, counties, universities, and other public-sector entities. Each row shows the entity's name, type, state, and status.

### Filtering and search

Use the filter bar at the top of the directory to narrow results:

| Filter | What it does |
|---|---|
| **Name search** | Free-text search across entity names. Debounced — results update as you type. |
| **Type** | Filter by entity type (K-12 district, city, county, university, community college, state agency, special district). |
| **State** | Filter to a single US state (two-letter abbreviation). |
| **Status** | Filter by entity status: active, dissolved, or inactive. |

Results are cursor-paginated. Click **Load more** to fetch additional records.

### Entity status

| Status | Meaning |
|---|---|
| **Active** | The entity is operational and receiving signals. |
| **Dissolved** | The organisation no longer exists (school closed, city merged, etc.). Signals stop; historical data is preserved. |
| **Inactive** | The entity exists but is not currently producing signals (data gap or scraper pending). |

## Entity profile

Click any entity card to open the entity profile at `/entities/<id>`. The profile contains:

### Overview

Key facts extracted from official sources:

- **Enrollment** (education entities) — number of students
- **Population** (government entities)
- **Annual budget** — formatted as $XM or $XB when available
- **Board meeting cadence** — how often the board meets (monthly, quarterly, etc.)
- **Primary website** — links to the entity's official site
- **Procurement portal** — direct link to the entity's bid portal if known
- **NCES LEAID / IPEDS Unit ID / Census GID** — official identifiers for cross-referencing

### Hierarchy

Entities can have a parent–child relationship (for example, a school within a district). The profile shows:

- **Parent entity** — a link to the parent if this entity is a sub-unit.
- **Sub-entities / Children** — a cursor-paginated list of child entities.

### Source citations

Links to the official data sources CivicSignals used to populate this entity record.

### Contacts

A list of contacts associated with this entity (described below).

## Contacts

Each entity profile includes a **Contacts** section showing staff contacts sourced from official `.gov` and `.edu` staff directories.

### Contact fields

| Field | Description |
|---|---|
| **Name** | Full name of the contact. |
| **Title** | Job title and department. |
| **Email** | Validated email address (when public). |
| **Phone** | Public phone number (when available). |
| **Source URL** | The official directory page this contact was sourced from. |
| **Last verified** | When CivicSignals last confirmed this contact's email was deliverable. |
| **Verified** | Boolean flag set to `true` when the contact's email has passed validation. |
| **Status** | One of `active`, `inactive`, `stale`, `bounced`, or `invalid` (see below). |

### Contact status

| Status | Meaning |
|---|---|
| **active** | The contact is current and the email has been successfully validated. |
| **inactive** | The contact record exists but is no longer at the entity (e.g. has left the role). |
| **stale** | The email has not been re-validated within the 30-day cycle. |
| **bounced** | A recent email validation returned a bounce. Treat this address with caution. |
| **invalid** | The address has been confirmed undeliverable or reported incorrect. |

### Email verification

CivicSignals validates contact emails on a 30-day cycle. After 3 consecutive bounces, the contact is hidden from suggestions but kept in the record for historical reference.

A **stale** or **bounced** status means the email address may no longer be deliverable — verify through a direct channel before using it in outreach.

### Contact export

Admins and Members can export contacts to CSV. Exports are quota-limited per plan:

| Plan | Contact exports per month |
|---|---|
| Solo | 50 |
| Starter | 500 |
| Pro | Unlimited |
| Enterprise / Self-hosted | Unlimited |

The current quota usage appears in **Settings → Billing → Usage**.

## Signal history

Individual signal records link back to the entity where they originated. To see all signals for a specific entity, use the **Signal feed** with an entity filter (available from the feed's filter chip bar once it ships in G1).

## Entity aliases

The same organisation may appear under multiple names across sources (for example, "Austin ISD", "Austin Independent School District", and "AISD"). CivicSignals uses fuzzy matching to canonicalise entity names and stores alternative names as `aliases`. Admins can manually merge duplicate entity records.

## Permissions

| Action | Admin | Member | Viewer |
|---|---|---|---|
| Browse entity directory | Yes | Yes | Yes |
| View entity profile | Yes | Yes | Yes |
| View contacts | Yes | Yes | Yes |
| Export contacts (quota) | Yes | Yes | No |
