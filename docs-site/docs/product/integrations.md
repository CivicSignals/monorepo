---
id: integrations
title: Integrations and billing
sidebar_label: Integrations and billing
slug: /product/integrations
---

# Integrations and billing

This page covers connecting CivicSignals to your CRM and communication tools, managing your subscription plan, and issuing API tokens.

## CRM integrations

CivicSignals supports push-only sync to **Salesforce** and **HubSpot** in MVP. When a signal is pushed from CivicSignals:

- If the entity does not exist in the CRM, CivicSignals creates a new **Account** (Salesforce) or **Company** (HubSpot).
- A **Lead** (Salesforce) or **Contact** (HubSpot) is created and linked to the account.
- Custom fields are populated: `civic_signal_id`, `civic_score`, `civic_signal_type`, and `civic_source_url`.
- A second push of the same signal updates the existing record — it does not create a duplicate.

:::info Push-only in MVP
Two-way CRM sync (reading stage and ownership back from the CRM) is planned for v2. In MVP, CivicSignals is the source that pushes; the CRM is the authoritative record.
:::

### Connecting Salesforce or HubSpot

Connecting a CRM is an admin-only action:

1. Go to **Settings → Integrations**.
2. Click **Connect** next to Salesforce or HubSpot.
3. Complete the OAuth flow (you will be redirected to Salesforce or HubSpot to authorise).
4. Map the ICP score to a custom CRM field if desired (CivicSignals creates the field if it is missing in Salesforce).

### Push failures

If a CRM push fails (for example, a required field is missing in Salesforce), CivicSignals:

1. Shows an error toast with the CRM's error message.
2. Sends a notification to the user's in-app inbox and by email, with a deeplink to **Settings → Integrations**.
3. Records the failure in the workspace audit log.

Fix the mapping error in **Settings → Integrations → [CRM]** and retry the push from the signal detail page.

### Reconnecting / rotating credentials

OAuth refresh tokens can expire or be revoked. If a push starts failing with an auth error, go to **Settings → Integrations → [CRM] → Reconnect** to re-run the OAuth flow. The previous connection is replaced and the audit log records the rotation.

## Slack integration

CivicSignals can post signals to a Slack channel per saved search. Slack connectivity is workspace-level (one OAuth install per workspace) and is configured in **Settings → Integrations → Slack**.

After connecting Slack, each saved search has a **Slack channel destination** setting where you choose the channel to post to.

:::info Saved searches coming soon
Saved search creation and Slack destination configuration will be available when the Saved Searches feature ships (see [Saved searches](/product/saved-searches)).
:::

### Slash command

Once installed, the `/civic search <query>` Slack slash command runs a Smart Search and posts the top results to the channel. This is subject to your plan's Smart Search rate limit.

## Plans and billing

CivicSignals Cloud offers three self-serve tiers and a quote-driven Enterprise plan:

| Plan | Price | Best for |
|---|---|---|
| **Solo** | $19 / seat / month | Individual practitioners |
| **Starter** | $49 / seat / month | Small sales teams |
| **Pro** | $149 / seat / month | Larger teams needing higher quotas |
| **Enterprise** | Contact sales | Organisations needing custom limits, SLA, and invoicing |

All plans include a **14-day free trial** with no credit card required.

### Usage meters

Go to **Settings → Billing** (`/settings/billing`) to see your current plan and usage for this billing period (calendar month, UTC). Usage meters track:

- **Contact exports per month** — how many contacts you have exported to CSV this billing month.
- **Smart searches per month** — how many natural-language searches have been run this billing month.
- **Saved searches** — how many saved searches currently exist in the workspace (not a time-windowed counter).

When a usage meter turns amber, you are approaching the plan limit. When it turns red, you have exceeded the limit and further actions of that type are blocked until the billing period resets or you upgrade.

### Changing plans

From **Settings → Billing**, click **Switch** on the plan you want to move to. Stripe calculates proration automatically for the current billing period. Plan changes take effect immediately.

Enterprise plans are quote-driven — click **Contact sales** or email `sales@civicsignals.io`.

### Billing portal

Click **Open billing portal** in **Settings → Billing** to open the Stripe Customer Portal in a new window. From there you can:

- Update payment methods.
- Download invoices.
- View full billing history.

### Trial expiry

If the trial expires without a payment method on file:

| Day | What happens |
|---|---|
| Day 0 | Trial begins. |
| Day 14 | Trial ends. Workspace enters `read_only`: signals still arrive, but CRM pushes and contact exports are blocked. |
| Day 28 | Workspace enters `suspended`: signal ingestion stops. |
| Day 88 | Workspace data is deleted. A warning email is sent 7 days before deletion. |

You can add a payment method at any point during the read-only or suspended period to reactivate the workspace immediately.

### Annual billing

Annual billing is available with a **15% discount** applied to the monthly rate. Switch between monthly and annual billing through the Stripe Customer Portal.

## API tokens

CivicSignals issues **API tokens** for programmatic access to the REST API. Tokens are managed from **Settings → API tokens** (`/settings/tokens`).

### Token types

| Type | Scope | Use |
|---|---|---|
| **Personal access token** | Scoped to the issuing user across their workspaces | Local development, personal scripts, SDK usage |
| **Workspace token** | Scoped to a single workspace | Server-to-server integrations, CI/CD, data pipelines |

### Creating a token

1. Go to **Settings → API tokens**.
2. Select the **Personal** or **Workspace** tab.
3. Click **New token**.
4. Give the token a name (for your reference) and choose an expiry.
5. Copy the token immediately — it is shown only once.

Workspace tokens require admin role to create or revoke.

### Using tokens

Include the token in the `Authorization` header:

```http
Authorization: Bearer <token>
```

For workspace-scoped API calls, also include the workspace ID:

```http
X-Workspace-Id: <workspace-uuid>
Authorization: Bearer <token>
```

See the [API reference](/api/intro) for full endpoint documentation.

### Revoking tokens

Revoke a token from **Settings → API tokens** by clicking **Revoke** next to it. Revocation is immediate — any in-flight requests using the revoked token will receive a 401 response.

## Permissions

| Action | Admin | Member | Viewer |
|---|---|---|---|
| View Integrations settings | Yes | No | No |
| Connect / disconnect CRM | Yes | No | No |
| View Billing settings | Yes | No | No |
| Change plan | Yes (owner) | No | No |
| Open Stripe portal | Yes (owner) | No | No |
| Create/revoke personal API tokens | Yes | Yes | No |
| Create/revoke workspace API tokens | Yes | No | No |
