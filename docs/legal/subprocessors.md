# Subprocessors

> **DRAFT — Pending legal review. Not legally effective until reviewed by counsel and published.**  
> **Last updated:** [DATE TO BE SET AT PUBLICATION]

CivicSignals uses the third-party service providers listed below ("sub-processors") to operate the CivicSignals Cloud platform. Each sub-processor is contractually bound to data protection obligations substantially equivalent to those in our [Data Processing Addendum](dpa.md).

We will provide 30 days' advance notice of changes to this list via email to workspace billing contacts.

---

## Infrastructure and Hosting

| Sub-processor | Country | Purpose | Data Processed |
|---|---|---|---|
| **Amazon Web Services (AWS)** | USA (us-east-1; eu-west-1 planned) | Cloud infrastructure: compute (ECS/EKS), managed database (RDS Postgres), object storage (S3), cache (ElastiCache Redis), CDN (CloudFront), WAF, Secrets Manager | All Customer Data at rest and in transit within our infrastructure |
| **Hetzner Online GmbH** | Germany / USA | Phase 0 bootstrap VPS hosting before AWS migration | All Customer Data at rest and in transit (Phase 0 only; replaced by AWS at Phase 1) |

---

## Payments and Billing

| Sub-processor | Country | Purpose | Data Processed |
|---|---|---|---|
| **Stripe, Inc.** | USA | Payment processing, subscription management, customer portal | Billing contact name and email, payment card data (Stripe stores card data; we store only customer ID, subscription ID, payment type, last-4 digits) |

---

## Email and Communications

| Sub-processor | Country | Purpose | Data Processed |
|---|---|---|---|
| **Postmark (Wildbit LLC / ActiveCampaign)** | USA | Transactional email (auth, invitations, integration alerts, digests) | Recipient email addresses, email content |
| **Resend (Resend, Inc.)** | USA | Transactional email (Phase 0 fallback / alternative to Postmark — see architecture) | Recipient email addresses, email content |

Note: One of Postmark or Resend is active at any given time based on deployment phase. Both are listed as sub-processors.

---

## AI / LLM Providers

CivicSignals uses large language models to extract structured signals from scraped public government documents. By default, Anthropic is the extraction provider for Cloud customers.

| Sub-processor | Country | Purpose | Data Processed | Notes |
|---|---|---|---|---|
| **Anthropic, PBC** | USA | Default LLM extraction provider (Claude models) | Snippets of public-domain scraped documents (government records, board minutes, bid notices) sent for extraction. PII is redacted from prompts where possible. | Default for Cloud customers. |
| **OpenAI, LLC** | USA | Alternative LLM extraction and embedding provider (GPT-4o, text-embedding-3-small) | Same as Anthropic above | Optional; used only if workspace is configured to use OpenAI. |

**Self-hosted note:** On CivicSignals Core (self-hosted), you choose the LLM provider (including local Ollama). No data is sent to Anthropic or OpenAI unless you configure those providers. You are the data controller for your self-hosted deployment.

---

## Error Tracking and Observability

| Sub-processor | Country | Purpose | Data Processed |
|---|---|---|---|
| **Sentry (Functional Software, Inc.)** | USA | Error tracking and performance monitoring | Sanitized error traces, stack traces, and performance data. PII scrubbing rules are applied: no request bodies, no Authorization headers, no Customer Data. Allowed headers: x-request-id, x-workspace-id, user-agent. |

**Note:** We use self-hosted Sentry or GlitchTip as an alternative where privacy constraints require it. Vendor TBD at launch.

---

## Analytics

| Sub-processor | Country | Purpose | Data Processed |
|---|---|---|---|
| **PostHog, Inc.** | USA (self-hosted by CivicSignals in our own infrastructure) | Product analytics — activation funnel, feature usage, retention cohorts | Usage events (page views, feature interactions). No PII in event properties. No data leaves our infrastructure — PostHog is self-hosted. |
| **Plausible Analytics** | EU (self-hosted or EU cloud) | Marketing site analytics | Aggregated page view statistics for `civicsignals.io` marketing pages only. No cookies, no personal identifiers. |

---

## Integrations (Customer-Configured)

The following sub-processors are engaged only when you actively configure the corresponding integration. Data is sent to these services on your behalf and under your CRM account.

| Integration | Sub-processor | Purpose | Data Processed |
|---|---|---|---|
| Salesforce integration | **Salesforce, Inc.** | CRM push of signal metadata | Signal data, entity data, contact info — as configured by your field mapping |
| HubSpot integration | **HubSpot, Inc.** | CRM push of signal metadata | Same as above |
| Slack integration | **Slack Technologies, LLC** | Signal notifications to Slack channels | Signal summaries, entity names |

These integrations are under your control. CivicSignals acts as your agent when pushing data to these services. Their privacy and data processing terms are your responsibility as the controller.

---

## Changes to This List

When CivicSignals adds, removes, or materially changes a sub-processor:

1. We will update this page and the "Last updated" date.
2. We will notify workspace billing contacts by email at least **30 days** before the change takes effect.
3. If you object to a new sub-processor on data protection grounds, contact privacy@civicsignals.io within 30 days of the notice.

---

## Questions

Contact privacy@civicsignals.io for questions about this list or our data processing practices.
