# Privacy Policy

> **DRAFT — Pending legal review. Not legally effective until reviewed by counsel and published.**  
> **Last updated:** [DATE TO BE SET AT PUBLICATION]

This Privacy Policy explains how CivicSignals, Inc. ("CivicSignals," "we," "us," or "our") collects, uses, shares, and protects information about you when you use our platform and services at `civicsignals.io` (the "Service").

---

## 1. Scope

This policy applies to:

- **CivicSignals Cloud** users and workspace members
- Visitors to `civicsignals.io` and related subdomains

It does **not** apply to self-hosted CivicSignals Core installations, where you are the data controller. It does not apply to third-party websites linked from the Service.

---

## 2. Information We Collect

### 2.1 Information You Provide

- **Account data:** name, email address, password (hashed; we never store the plaintext), and optional profile details.
- **Workspace data:** ICP definitions, saved searches, pipeline items, FOIA request records, and other content you create in the Service.
- **Billing data:** we collect billing contact information. Payment card data is processed and stored by Stripe; we store only your Stripe customer ID, subscription ID, and payment method type and last four digits.
- **Communications:** if you contact us by email or through support, we retain those communications.

### 2.2 Information We Collect Automatically

- **Usage data:** pages visited, features used, button clicks, and similar interaction events — collected via PostHog (self-hosted by us in our own infrastructure; no data leaves to PostHog's cloud).
- **Log data:** IP address, user-agent, request ID, workspace ID, response time, and HTTP status codes for each API and web request. We do not log request bodies containing customer data at INFO level; bodies are sampled at DEBUG with a reduced retention period.
- **Authentication events:** login times, MFA attempts, session creation/invalidation.

### 2.3 Information From Third Parties

- **OAuth providers (Google):** if you sign in via Google, we receive your name, email address, and profile picture URL from Google.
- **CRM integrations (Salesforce, HubSpot):** when you connect a CRM, we receive OAuth tokens and, during pushes, receive confirmation of the created/updated CRM record ID. We do not read CRM data in MVP.
- **Stripe:** we receive webhook events (subscription changes, payment outcomes) which we process to manage your account status.

### 2.4 Public-Domain Data We Process

CivicSignals scrapes publicly available government and education websites to extract buying signals. This data includes:

- **Public-sector entity data:** government agency names, addresses, contact pages — sourced from official .gov and .edu websites.
- **Public-sector contact records:** names, titles, work email addresses, and phone numbers of public servants in their official roles — sourced exclusively from official agency websites, not from social networks or purchased data.
- **Signal content:** procurement notices, budget documents, board meeting minutes, grant announcements, and similar public government records.

This is public information about public servants acting in their public roles, sourced from official government websites under applicable public records law.

---

## 3. How We Use Your Information

| Purpose | Legal Basis (GDPR) | Data Used |
|---|---|---|
| Providing the Service | Contract performance | Account data, workspace data, usage data |
| Authentication and security | Legitimate interest | Account data, log data, authentication events |
| Billing and payments | Contract performance | Billing data, Stripe events |
| Product analytics and improvement | Legitimate interest (opt-out available) | Usage data (anonymized aggregates) |
| Customer communications | Contract performance / legitimate interest | Email address, communication history |
| Legal compliance | Legal obligation | Audit log, account data |
| Signal extraction (public-domain data) | Legitimate interest / public interest | Public-domain entity and contact data |

We do **not** sell your personal information. We do not use Customer Data (your ICP, searches, pipeline, FOIA records) to train machine learning models without explicit consent.

---

## 4. Data Sharing

### 4.1 Sub-processors

We share data with sub-processors listed at [docs/legal/subprocessors.md](subprocessors.md) (published at `civicsignals.io/legal/subprocessors`). We contractually require sub-processors to maintain appropriate data protection standards.

Key sub-processors: AWS (infrastructure), Stripe (payments), Postmark/Resend (email), Anthropic and optionally OpenAI (LLM extraction), Sentry (error tracking).

### 4.2 Service Operation

Within CivicSignals, access to personal data is limited to employees and contractors who need it to operate and improve the Service.

### 4.3 Legal Requirements

We may disclose your information if required by law, court order, or valid governmental process, or to protect the rights, property, or safety of CivicSignals, our users, or the public.

### 4.4 Business Transfers

In connection with a merger, acquisition, bankruptcy, or sale of all or substantially all assets, your information may be transferred to the acquiring entity, subject to the same privacy protections.

### 4.5 Aggregated and De-identified Data

We may share aggregated, de-identified data (e.g., "X% of signals in the SLED market are RFPs") that cannot reasonably identify you.

---

## 5. Data Retention

| Data Type | Retention |
|---|---|
| Account data | Retained while your account is active. Deleted within 30 days of account deletion request, subject to applicable audit log obligations. |
| Workspace data (ICP, searches, pipeline, FOIA) | Retained while workspace is active. 90-day soft-delete after workspace cancellation, then hard-deleted. |
| Signals | 24 months (active); archived read-only for 12 additional months on Pro/Enterprise; deleted on Solo/Starter after 24 months. |
| Raw scraped documents | 36 months from fetch date, then deleted from object storage (a provenance hash and URL are retained indefinitely). |
| Contact records | Retained as long as confirmed by a live source. Archived after 12 months without re-scrape confirmation. |
| Audit log | 90 days (Solo/Starter); 12 months (Pro); 7 years (Enterprise). |
| Log data | 30 days hot, 1 year archived in compressed cold storage. |
| Billing data | As required by tax and financial record-keeping obligations (minimum 7 years). |

---

## 6. Your Rights

Depending on your location, you may have the following rights regarding your personal data:

- **Access:** Obtain a copy of your personal data. Available in-product at Account Settings → Export.
- **Correction:** Correct inaccurate data. Available in-product.
- **Erasure ("right to be forgotten"):** Request deletion of your account and personal data. Submit via Account Settings → Delete Account or email privacy@civicsignals.io.
- **Portability:** Receive your personal data in a machine-readable format (JSON). Same as access export.
- **Objection:** Object to processing based on legitimate interest. Contact privacy@civicsignals.io.
- **Restriction:** Request that we restrict processing in certain circumstances.
- **Opt-out of sale / sharing (CCPA):** We do not sell personal information. The "Do Not Sell or Share" right is inherently honored.

**Contact records:** If you are a public-sector professional whose contact information appears in CivicSignals as a result of scraping your official agency's website, you may submit an objection by emailing privacy@civicsignals.io with your name and the URL of the official page where your information appeared. A self-service API endpoint (`POST /contacts/{id}/object`) is planned for a future release. We will remove your record from search/extraction results within 30 days and suppress re-discovery for 24 months. We retain an opaque suppression hash to prevent re-indexing.

To exercise any of these rights, contact privacy@civicsignals.io. We will respond within 30 days. We may ask you to verify your identity before fulfilling your request.

---

## 7. Data Security

We implement technical and organizational measures to protect your data:

- TLS 1.2+ encryption for all data in transit; HSTS preloaded
- AES-256 encryption at rest for all database volumes, object storage, and cache snapshots
- Password hashing with bcrypt (cost factor 12)
- Multi-factor authentication (TOTP) available on all paid plans; required for workspace admins
- API tokens hashed at rest; revealed only once at creation
- Workspace isolation enforced at the ORM layer; no cross-workspace data access
- Structured audit logging of all authenticated actions
- Annual external penetration testing (beginning month 6 after Cloud GA — see R2)
- SOC 2 Type I in progress; Type II targeted for month 18

A more detailed description of our security practices is at [docs/legal/security.md](security.md).

---

## 8. LLM Processing of Documents

When you use CivicSignals Cloud, documents scraped from public sources are processed by large language model (LLM) providers to extract structured signals. By default, this uses Anthropic's API. You may also configure OpenAI.

We minimize what is sent to LLM providers:
- We do not send your ICP definitions or customer-specific configuration to LLM providers.
- We redact known contact PII (email addresses) from extraction prompts unless the extraction task explicitly requires them.
- Raw document content (public government records) is sent to the LLM for extraction; this content is public-domain.

On self-hosted CivicSignals Core, you control the LLM provider (including local-only Ollama) and are responsible for the data governance implications.

---

## 9. Cookies

Our use of cookies is described in the [Cookie Policy](cookie-policy.md). In summary: the app uses only functional and security cookies (session, CSRF). The marketing site uses Plausible Analytics (cookieless, privacy-respecting). We do not use third-party advertising cookies.

---

## 10. Children's Privacy

The Service is not directed to children under 18 and we do not knowingly collect personal information from children. If you believe we have collected information from a child, contact privacy@civicsignals.io.

---

## 11. International Transfers

CivicSignals is currently hosted in the United States (AWS us-east-1). If you are located in the European Economic Area, the United Kingdom, or Canada, your data is transferred to and processed in the United States. We rely on Standard Contractual Clauses (SCCs) where required for lawful international transfers under GDPR. Our DPA includes the appropriate SCCs.

A European Economic Area hosting option is planned for Enterprise customers at month 12 after Cloud GA.

---

## 12. FERPA and HIPAA

**FERPA:** CivicSignals tracks educational institutions as public entities (procurement, governance, leadership data from official websites) but does not collect, store, or process student education records. FERPA does not apply to our pipeline. We publish a FERPA alignment statement for school-district buyers on request.

**HIPAA:** CivicSignals does not process Protected Health Information (PHI). Health-adjacent public entities may be tracked; no clinical or patient data flows through the platform. CivicSignals is not a HIPAA Business Associate and does not sign BAAs.

---

## 13. Changes to This Policy

We will notify you of material changes to this Policy by email and by posting an updated version at `civicsignals.io/legal/privacy` at least 30 days before the changes take effect. The "Last updated" date at the top of this page will reflect the most recent revision.

---

## 14. Contact

**Privacy inquiries:** privacy@civicsignals.io  
**Data subject rights requests:** privacy@civicsignals.io  
**Security concerns:** security@civicsignals.io  
**General inquiries:** hello@civicsignals.io

**CivicSignals, Inc.**  
civicsignals.io
