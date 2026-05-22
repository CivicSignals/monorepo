---
id: foia
title: FOIA tracker
sidebar_label: FOIA tracker
slug: /product/foia
---

# FOIA tracker

The **FOIA tracker** helps you draft, send, and follow up on public-records requests (Freedom of Information Act requests and their state equivalents). The tracker lives at `/foia`.

## Why track FOIA requests in CivicSignals?

Public-records requests are a powerful research tool for understanding government procurement: historical contracts, vendor pricing, board communications, and budget documents that are not published online. CivicSignals links your FOIA requests to the entities and signals you are already tracking, so the research stays connected to your pipeline.

## Templates

CivicSignals ships pre-written request templates for eight US jurisdictions. Templates use placeholder variables (for example, `{requester_name}`, `{date}`, `{agency}`) that you fill in when creating a request.

| Jurisdiction code | Law |
|---|---|
| `US-FOIA` | Federal Freedom of Information Act |
| `CA-PRA` | California Public Records Act |
| `CO-CORA` | Colorado Open Records Act |
| `FL-PRA` | Florida Public Records Act |
| `IL-FOIA` | Illinois Freedom of Information Act |
| `NY-FOIL` | New York Freedom of Information Law |
| `TX-PIA` | Texas Public Information Act |
| `WA-PRA` | Washington Public Records Act |

Templates are global reference data — you can view them without selecting a workspace. All templates are marked **draft** pending legal review; consult your legal counsel before relying on them.

:::info Missing your state?
If your state is not listed, choose **No template — freeform** when creating a request and write the body yourself. Community contributions for additional state templates are welcome via the [recipes repository](https://github.com/CivicSignals/monorepo).
:::

## Creating a FOIA request

Click **+ New request** on the `/foia` page. The creation form has the following fields:

| Field | Required | Description |
|---|---|---|
| **Template** | No | Optionally pick a jurisdiction template. The body auto-populates with the rendered template text. |
| **Target entity ID** | Yes | The UUID of the entity you are requesting records from. Find it in the [Entity Directory](/product/entities). |
| **Subject** | Yes | A short description of the records requested (up to 512 characters). Appears in the list view. |
| **Request body** | Yes | The full text of the public-records request. If you selected a template, edit the placeholder values directly in the text area. |
| **Submission method** | No | How you will submit the request: Manual, Email, Online portal, Mail, or In person. Defaults to Manual. |
| **Submission target** | No | The email address, URL, or mailing address to submit to. |

Click **Create draft** to save the request in `draft` status. The app redirects to the request detail page.

## Request detail

Each request has its own page at `/foia/<id>` with:

- **Request body** — the full text of the request as submitted or drafted.
- **Status badge** — the current status (see below).
- **Status timeline** — a chronological log of every status transition.
- **Transition controls** — buttons to advance the request to the next status.
- **Reminder configuration** — controls for follow-up reminders (see below).

## Status state machine

FOIA requests move through a linear state machine:

```
draft → sent → acknowledged → responded
                           ↘ denied
         ↘ withdrawn
```

| Status | Meaning |
|---|---|
| **draft** | The request has been created but not yet sent. Editable. |
| **sent** | The request has been submitted to the agency. |
| **acknowledged** | The agency has confirmed receipt of the request. |
| **responded** | The agency has provided records in response to the request. |
| **denied** | The agency has denied the request. |
| **withdrawn** | You withdrew the request before receiving a response. |

Only `draft` requests can be edited (subject, body, submission method, and target). Attempting to edit a non-draft request returns an error.

### Advancing the status

Use the **transition controls** on the detail page to move the request forward. CivicSignals records the timestamp of each transition automatically:

- `sent_at` is set when you mark the request as **sent**.
- `ack_at` is set when you mark it as **acknowledged**.
- `response_at` is set when you mark it as **responded**.

Illegal transitions (for example, jumping from `draft` directly to `responded`) are rejected with a clear error message.

## Reminders

CivicSignals can send you follow-up reminder emails when a submitted request goes unanswered. The default reminder schedule is:

- **First reminder:** 14 days after `sent_at`.
- **Repeat interval:** weekly after the first reminder.
- **Maximum reminders:** unlimited by default.

Customise the reminder schedule from the detail page:

| Setting | Default | Description |
|---|---|---|
| **Reminders enabled** | Yes | Toggle reminders on or off for this request. |
| **Initial delay (days)** | 14 | Days after `sent_at` before the first reminder is sent. |
| **Repeat interval (days)** | 7 | Days between subsequent reminders. |
| **Maximum reminders** | 0 (unlimited) | Set a cap on how many reminders are sent. |

Reminders stop automatically when the request reaches a terminal status (`responded`, `denied`, `withdrawn`).

## Listing and filtering requests

The `/foia` list page shows all FOIA requests in your workspace. Filter by:

- **Status** — show only requests in a given status.
- **Entity** — show only requests for a specific entity (enter the entity UUID).

Results are cursor-paginated (oldest first).

## Permissions

| Action | Admin | Member | Viewer |
|---|---|---|---|
| View all workspace FOIA requests | Yes | Yes | No |
| Create a FOIA request | Yes | Yes | No |
| Edit a draft request | Yes | Yes (own only) | No |
| Transition request status | Yes | Yes (own only) | No |
| Configure reminders | Yes | Yes (own only) | No |
