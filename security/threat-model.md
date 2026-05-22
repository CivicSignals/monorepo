# CivicSignals Threat Model

**Status:** DRAFT — pending security review and R2 external pentest validation  
**Version:** 0.1  
**Last updated:** 2026-05-22  
**Owner:** Engineering / Security  
**Next review:** Before R2 pentest engagement; then annually  
**Related tasks:** R1 (this document), R2 (external pentest), B7 (RBAC), B8 (API tokens), D1 (recipe runner / SSRF), E2 (LLM gateway), K1 (integrations), L3 (webhook HMAC)

---

## 1. System Overview

CivicSignals is a multi-tenant SaaS platform with a self-hostable open-source core. It ingests, extracts, and scores buying signals from public-sector websites (.gov, .edu, bid portals, board minutes, grants, FOIA responses) and surfaces those signals to sales teams via a web UI, API, and CRM integrations.

### 1.1 Process Types (Six-Process Modulith)

The entire backend is deployed from a **single Docker image** selected by the `PROCESS_TYPE` environment variable. All six types share the same codebase and are separated by queue routing:

| Process | Description | Privileged access |
|---|---|---|
| `api` | FastAPI/uvicorn, serves HTTP | Postgres (via PgBouncer), Redis, S3 |
| `worker_ingest` | Celery, fetches URLs per recipe | Outbound HTTP to arbitrary public URLs, Postgres, S3 |
| `worker_extract` | Celery, runs LLM extraction pipeline | LLM provider APIs (Anthropic/OpenAI/Ollama), Postgres, S3 |
| `worker_score` | Celery, scores and dedupes signals | Postgres, pgvector |
| `worker_notify` | Celery, sends email/Slack/webhook | Email SMTP, Slack API, outbound HTTPS to customer webhooks |
| `scheduler` | Celery beat singleton | Redis (distributed lock), Postgres |

### 1.2 Data Stores

| Store | Role | Sensitivity |
|---|---|---|
| PostgreSQL 16 (via PgBouncer port 6432) | All application data, multi-tenant by `workspace_id` | HIGH — contains all user PII, signals, ICP configs, API tokens (hashed), OAuth tokens (encrypted) |
| PostgreSQL direct (port 5432) | Alembic migrations, LISTEN/NOTIFY, long exports | HIGH (same data) |
| Redis | Celery broker + cache (feed pages, ICP score caches 1h TTL) | MEDIUM — queue payloads may contain signal metadata |
| S3 / MinIO | Raw scraped HTML/PDFs, FOIA attachments, CSV exports | MEDIUM-HIGH — raw documents may contain adversarial content |
| pgvector | Signal embeddings for similarity search | LOW — numerical vectors, not raw content |

### 1.3 External Integrations

| Integration | Protocol | Direction | Data Shared |
|---|---|---|---|
| Salesforce | OAuth 2.0 + REST | Outbound push | Signal metadata, entity data, contact info |
| HubSpot | OAuth 2.0 + REST | Outbound push | Same as above |
| Slack | OAuth + Web/Events API | Outbound + commands | Signal summaries |
| Stripe | HTTPS + webhooks | Bidirectional | Billing metadata (we hold customer ID, last-4, subscription) |
| Anthropic | HTTPS REST | Outbound | Document content for LLM extraction |
| OpenAI | HTTPS REST | Outbound | Same; also generates embeddings |
| Ollama | Local HTTP | Outbound (self-host only) | Same as above, local |
| Resend / Postmark | SMTP + API | Outbound | Email templates, user addresses |
| Sentry | HTTPS | Outbound | Sanitized error traces (no PII per scrubbing rules) |

### 1.4 Deployment Variants

- **Cloud (Phase 0):** Single Hetzner VPS behind nginx + Let's Encrypt; later AWS ECS/EKS + RDS + S3
- **Self-host (docker-compose / Helm):** Operator-managed; operator is responsible for secrets, TLS, and network configuration
- **Development:** Local docker-compose with Mailpit, MinIO

---

## 2. Trust Boundaries

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  UNTRUSTED INTERNET                                                          │
│  ┌────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐  │
│  │ Public web     │   │ End users (browsers)  │   │ API consumers        │  │
│  │ sources (.gov, │   │ - Authenticated users │   │ - Bearer token       │  │
│  │ .edu, portals) │   │ - OAuth flows         │   │ - Webhook receivers  │  │
│  └───────┬────────┘   └──────────┬────────────┘   └──────────┬───────────┘  │
└──────────┼─────────────────────┼─────────────────────────────┼──────────────┘
           │ scheduled crawl     │ HTTPS (TLS 1.2+)            │ HTTPS
           │ (outbound HTTP      │ + HSTS                      │
           │  from ingest worker)│                             │
┌──────────┼─────────────────────┼─────────────────────────────┼──────────────┐
│  DMZ / nginx reverse proxy (TLS termination, WAF)            │              │
│  ┌────────┴──────────────────────┴──────────────────────────  ┘             │
│  │  TLS → HTTP (internal)                                                   │
└──┼───────────────────────────────────────────────────────────────────────────┘
   │
┌──┼───────────────────────────────────────────────────────────────────────────┐
│  │  APPLICATION TRUST ZONE (internal network / VPC private subnet)          │
│  ▼                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐│
│  │  api process (FastAPI/uvicorn)                                          ││
│  │  - Resolves workspace from auth token on EVERY request                  ││
│  │  - Sets workspace context; SQLAlchemy listener enforces it on queries   ││
│  │  - Validates X-Workspace-Id header matches token's workspace            ││
│  └────────────┬────────────────────────────────────────────────────────────┘│
│               │ Celery tasks (Redis queue)                                   │
│  ┌────────────▼────────────────────────────────────────────────────────────┐│
│  │  worker_ingest   worker_extract   worker_score   worker_notify          ││
│  │  scheduler                                                              ││
│  └────────────┬──────────────────────────────────────────────────────────┬─┘│
│               │                                                          │   │
│  ┌────────────▼──────┐   ┌──────────────────┐   ┌─────────────────────  ┘   │
│  │  PgBouncer        │   │  Redis           │   │  S3 / MinIO             │  │
│  │  (port 6432,      │   │  (Celery broker  │   │  (raw docs, FOIA,      │  │
│  │  transaction mode)│   │   + cache)       │   │   exports)              │  │
│  └────────────┬──────┘   └──────────────────┘   └─────────────────────────┘  │
│               │                                                               │
│  ┌────────────▼──────┐                                                        │
│  │  PostgreSQL 16    │                                                        │
│  │  + pgvector       │                                                        │
│  └───────────────────┘                                                        │
└───────────────────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────────────────┐
│  EXTERNAL SERVICES (outbound only; mTLS or API key auth)                      │
│  Anthropic API  ·  OpenAI API  ·  Salesforce  ·  HubSpot  ·  Slack           │
│  Stripe  ·  Postmark/Resend  ·  Sentry                                        │
└───────────────────────────────────────────────────────────────────────────────┘
```

### Trust Boundary Summary

| Boundary | Controls crossing it |
|---|---|
| Internet → nginx | TLS termination, AWS WAF (OWASP managed rules, rate limits), HSTS |
| Browser → api | Bearer token or session cookie (HttpOnly, Secure, SameSite=Lax); CSRF double-submit for cookie-auth mutations |
| api → Postgres | PgBouncer transaction-mode; workspace-scoped queries enforced by SQLAlchemy event listener |
| api → Redis | Network isolation; no authenticated Redis in dev (operator must enable AUTH in production) |
| api → S3/MinIO | IAM role (cloud) or MinIO access key (self-host); path-prefix per workspace |
| workers → LLM providers | API key per provider in Secrets Manager; all calls through `llm_gateway.py` |
| workers → public web | Outbound HTTP from `worker_ingest`; SSRF controls documented in §4.3 |
| api → external integrations | OAuth 2.0 tokens (per workspace); stored encrypted; never logged |

---

## 3. Data Classification

### 3.1 Data Classes

| Class | Description | Examples | Sensitivity |
|---|---|---|---|
| **PII — Account** | Data identifying CivicSignals users and workspace members | Name, email, hashed password, MFA secret, billing last-4 | HIGH |
| **PII — Contact** | Public-sector contact records scraped from official .gov/.edu sites | Name, title, work email, phone (from official directory pages) | MEDIUM — public role information; still subject to GDPR/CCPA objection |
| **Workspace data** | Customer-created configuration and activity | ICP definitions, saved searches, pipeline items, FOIA requests, audit events | HIGH — business-sensitive |
| **Signals** | Extracted structured buying signals | Signal type, entity, dates, dollar amounts, extracted text | HIGH — the core product; workspace-exclusive |
| **Raw scraped documents** | HTML, PDFs, or other content fetched from public sources | Board-minute PDFs, council meeting agendas, bid portal pages | MEDIUM — public origin, but may include adversarial content |
| **Secrets** | API keys, OAuth tokens, signing secrets | Database passwords, LLM API keys, Salesforce OAuth tokens, webhook signing secrets | CRITICAL |
| **Audit log** | Immutable record of authenticated actions | Login events, role changes, API token issuance, outbound pushes | HIGH — compliance-critical |

### 3.2 Data Flows Requiring Special Attention

1. **OAuth tokens for Salesforce/HubSpot** are workspace secrets: encrypted at rest (AES-256), never logged, transmitted only over HTTPS, rotated on disconnect.
2. **LLM extraction prompts** may contain snippets of scraped content (potentially adversarial). See §4.4 (prompt injection).
3. **Contact PII** from .gov/.edu pages flows through extraction → Postgres → CRM push. At each stage workspace isolation and data minimization apply.
4. **FOIA response PDFs** may contain sensitive public records; they are stored in S3 under workspace-scoped paths and never indexed globally.

---

## 4. STRIDE Threat Analysis

### 4.1 Multi-Tenant Isolation (workspace_id scoping)

**Asset:** Signals, ICP configs, contacts, audit logs belonging to a workspace.

| STRIDE | Threat | Existing / Planned Mitigations | Residual Risk |
|---|---|---|---|
| **Spoofing** | Attacker forges or guesses `X-Workspace-Id` header to access another workspace | The workspace is resolved from the authenticated token, not from the header. The header is only used for routing hints when a user belongs to multiple workspaces and must match the token's workspace set. | LOW |
| **Tampering** | Attacker modifies workspace_id in a query or request body | ORM-layer enforcement: a SQLAlchemy event listener injects `WHERE workspace_id = :ctx_workspace_id` on every workspace-scoped table. Tests assert that any query on workspace-scoped tables without the workspace context fails the build. | LOW |
| **Repudiation** | Workspace member denies a privileged action | Append-only audit log (`audit_event` table) for all authenticated actions. API-layer enforcement; direct DB access is not available to users. | LOW |
| **Information Disclosure** | Query returns rows from another workspace due to missing filter | Defense-in-depth: (1) SQLAlchemy listener, (2) integration tests that run cross-workspace queries and assert they return empty, (3) schemathesis property-based API tests. | LOW-MEDIUM — relies on correctness of the listener; any bypass is high severity. **Action R2:** pentest workspace isolation paths. |
| **Denial of Service** | Large query or bulk action in one workspace degrades others | Per-workspace usage metering (N3); soft/hard limits (N4); WAF rate limits. | MEDIUM — not yet implemented at MVP. |
| **Elevation of Privilege** | User in role `viewer` performs `admin` action | RBAC enforced in API decorators (B7); role stored server-side; no client-side elevation. | LOW pending B7 completion. |

**Key mitigations:** `workspace_id` isolation is the single highest-value invariant in the system. A bypass is classified as Critical severity. See B7, B8, and the SQLAlchemy event listener in `db.py`.

---

### 4.2 API Authentication and Tokens

**Asset:** User sessions, API tokens, bearer credentials.

| STRIDE | Threat | Existing / Planned Mitigations | Residual Risk |
|---|---|---|---|
| **Spoofing** | Stolen session cookie or API token used by attacker | Tokens hashed at rest (B8). Sessions: HttpOnly Secure SameSite=Lax cookie, 24h sliding / 30d absolute. Session invalidation on password change and MFA enable. | MEDIUM — token theft via XSS or MitM is the main remaining vector. |
| **Spoofing** | Credential stuffing on `/auth/login` | Rate limiting at WAF and application layer: 5 attempts / 15 min / IP; 20 attempts / 15 min / account; exponential backoff. | MEDIUM — WAF not yet deployed in Phase 0. Application-layer limits are planned (B1). |
| **Tampering** | JWT algorithm confusion (`alg: none`) | API uses opaque session IDs stored server-side for cookie-auth. Bearer tokens are hashed workspace-scoped API keys, not JWTs in MVP. No JWT verification surface. | LOW |
| **Repudiation** | Attacker denies creating an API token | API token creation is an audited action (B9). | LOW |
| **Information Disclosure** | API key leaked in logs or error messages | Structured logging rules never log Authorization headers. gitleaks pre-commit hook + CI scan. Sentry PII scrubbing rules exclude all headers except an explicit allowlist. | LOW-MEDIUM — operational risk on self-host where operator controls logging config. |
| **Denial of Service** | Token brute-force flood | Rate limits per §B1, WAF (cloud). | MEDIUM on self-host (no WAF). |
| **Elevation of Privilege** | Narrow-scope token used to call higher-privilege endpoint | Per-token scopes enforced at the endpoint level (B8). Scopes checked in `require_scope()` dependency. | LOW pending B8 completion. |

---

### 4.3 Ingestion Pipeline — SSRF and Adversarial Content

**Asset:** Internal network; integrity of the raw document store; scraper worker process.

The `worker_ingest` process fetches arbitrary URLs as specified by scraper recipes. This is the highest-risk external-facing component.

| STRIDE | Threat | Existing / Planned Mitigations | Residual Risk |
|---|---|---|---|
| **Spoofing** | Malicious recipe (community-contributed) redirects ingest worker to internal services (SSRF) | Recipe allowlist: only community-curated recipes reach the production scraper fleet. New recipes require human approval before merging to the default recipe set. Recipe runner enforces: (1) allowlist of permitted URL schemes (`http`, `https` only); (2) IP address blocklist (RFC 1918 ranges, loopback, link-local, metadata endpoint `169.254.169.254`); (3) DNS rebinding protection by resolving hostname before opening socket and checking the resolved IP against the blocklist; (4) redirect following is bounded and re-checked after each hop. **Task D1** owns this control. | MEDIUM — D1 controls not fully implemented yet. Any bypass could expose internal metadata endpoints. |
| **Tampering** | Adversarial HTML in scraped page exploits HTML parser or PDF library | Raw documents are stored as-is in S3 and never executed. HTML sanitization (DOMPurify or equivalent) is applied before any content is rendered in-app. PDFs are rendered via PDF.js in a sandboxed iframe with tight CSP. | MEDIUM — parser vulnerabilities (pdfplumber, lxml) are a supply-chain risk. |
| **Repudiation** | A recipe fetches a page that later changes; operator disputes what was scraped | Ingestion stores a content-addressed hash + URL + fetch timestamp for every raw document. Provenance is immutable. | LOW |
| **Information Disclosure** | Scraper error response leaks internal stack trace to the source site | Worker catches all exceptions and logs structured errors internally; no stack traces are sent in HTTP requests. User-Agent is set to a documented `CivicSignals/x.y (https://civicsignals.io/bot)`. | LOW |
| **Denial of Service** | Source site triggers slow-HTTP or infinite-redirect trap against ingest worker | Per-recipe timeout (configurable, default 30s); max-redirect limit (10); connection timeout (10s). `robots.txt` is honored; crawl-delay headers are respected. | LOW |
| **Elevation of Privilege** | Malicious recipe redirects to a cloud metadata endpoint to obtain IAM credentials | SSRF blocklist explicitly includes `169.254.169.254` (AWS/GCP/DO metadata), `fd00::/8`, `fc00::/7`. Enforced at the recipe runner level before each outbound request, including after redirects. | HIGH risk if not implemented; LOW when D1 controls are in place. |

**Special note — scraped content as LLM input:** See §4.4 below.

---

### 4.4 LLM Gateway — Prompt Injection

**Asset:** Integrity of extracted signals; LLM provider API budget; other workspaces' data.

All LLM access routes through `llm_gateway.py`. No module calls a vendor SDK directly.

| STRIDE | Threat | Existing / Planned Mitigations | Residual Risk |
|---|---|---|---|
| **Spoofing** | Adversarial content embedded in a scraped document instructs the LLM to adopt a different identity ("Ignore all previous instructions...") | Prompt structure: system prompt is hardcoded (versioned in `apps/api/prompts/`); user content is inserted as a clearly-delimited substring (XML tags or similar). Gateway validates that the model output conforms to the expected JSON schema; non-conforming responses are rejected and retried or dead-lettered. | MEDIUM — prompt injection is an unsolved problem at the model level. The extraction output schema validation is the primary control. |
| **Tampering** | Injected content causes the LLM to fabricate or corrupt extracted signal fields | Schema validation rejects malformed output. The gateway tracks extraction confidence; very low-confidence extractions are flagged for human review rather than stored as facts. | MEDIUM |
| **Information Disclosure** | Adversarial document causes LLM to leak system prompt or other documents from context | Prompt versioning allows rapid rotation if a prompt is found to be leakable. The gateway does not place multiple workspaces' data in the same context window. | LOW — single-workspace contexts |
| **Information Disclosure** | LLM provider receives PII from extraction prompts | The gateway redacts known entity contact emails from extraction prompts unless the extraction task explicitly requires them. BYO key on Core: the operator controls the provider and must accept their data policy. Cloud: only Anthropic (default) and optionally OpenAI receive extraction content; users are informed in the privacy policy. | MEDIUM — depends on correct redaction implementation in gateway. |
| **Denial of Service** | Adversarial document triggers extremely expensive LLM call (token flooding) | Per-workspace token budget enforced in the gateway (E2). Hard cap on input context length per extraction call. Extraction of documents >100 pages is truncated with `truncated: true` flag. | LOW |
| **Elevation of Privilege** | Injected content instructs LLM to return data from a different workspace | Gateway constructs each prompt independently with single-workspace context. Output is validated against the workspace's schema. No multi-workspace context windows. | LOW |

---

### 4.5 Webhook Signing and Outbound Integrations

**Asset:** Integrity of webhook deliveries; OAuth tokens for Salesforce/HubSpot/Slack; customer webhook endpoints.

| STRIDE | Threat | Existing / Planned Mitigations | Residual Risk |
|---|---|---|---|
| **Spoofing** | Attacker posts a forged webhook event to a customer's receiver | CivicSignals signs every outbound webhook with HMAC-SHA256 using a workspace-scoped secret. `X-Civic-Signature` header; 5-minute replay window. (Task L3.) | LOW when L3 is implemented |
| **Spoofing** | Attacker intercepts Stripe webhook and replays it | Stripe webhook signature verified (`STRIPE_WEBHOOK_SECRET`); Stripe timestamp-based replay protection (5-minute window) | LOW |
| **Tampering** | Outbound webhook payload altered in transit | HMAC-SHA256 signature covers the entire payload body. | LOW |
| **Repudiation** | Customer denies receiving a webhook delivery | Delivery log per webhook subscription (L3): timestamp, response code, response body snippet. | LOW |
| **Information Disclosure** | OAuth token for Salesforce/HubSpot leaked | Tokens stored encrypted at rest. Never logged. Transmitted only over HTTPS. Scope limited per integration. | LOW-MEDIUM — encrypted storage implementation must be audited (K1). |
| **Denial of Service** | Worker_notify floods a customer's webhook receiver with retries | Retry policy: exponential backoff, max 10 retries over 24h, then dead-letter. Circuit breaker on sustained 4xx/5xx from an endpoint. | LOW |
| **Elevation of Privilege** | SSRF via customer-configured webhook URL | Webhook URL is validated at registration time against the same SSRF blocklist used in the ingest pipeline (RFC 1918, loopback, metadata endpoint). | MEDIUM pending L3 implementation. |

---

### 4.6 Secret Handling

**Asset:** Database credentials, LLM API keys, OAuth client secrets, session signing secrets, webhook signing secrets.

| STRIDE | Threat | Existing / Planned Mitigations | Residual Risk |
|---|---|---|---|
| **Spoofing / Disclosure** | Secrets committed to the repository | gitleaks pre-commit hook + CI scan blocks secrets in code. `.env.example` contains only placeholder values. | LOW |
| **Disclosure** | Secrets exposed via environment variable leak in error responses | Error handlers never include environment variables or request context beyond the structured log fields allowlist. | LOW |
| **Disclosure** | Self-host operator stores secrets insecurely (plaintext in docker-compose.yml) | `.env.example` guidance discourages plaintext; a hardening guide (`docs/self-host/hardening.md`) will cover sealed-secrets / Vault alternatives. Ultimately, operator responsibility on self-host. | MEDIUM — operator risk; documented responsibility boundary |
| **Disclosure** | Cloud secrets leaked via Sentry or log aggregation | Sentry PII scrubbing rules; structured log rules never log full env variables; gitleaks CI prevents accidental log prints of secrets. | LOW |
| **Tampering** | AWS Secrets Manager rotation breaks a running service | Rotation is scheduled on 90-day cycles; app processes support IRSA token refresh without restart. Runbook covers forced rotation. | LOW |

---

### 4.7 Self-Host Deployment Security

**Asset:** Operator's deployment environment; CivicSignals data in operator's custody.

| STRIDE | Threat | Existing / Planned Mitigations | Residual Risk |
|---|---|---|---|
| **Spoofing** | Attacker serves a malicious container image to the operator | Container images are signed with cosign; SBOM attached per release. Operators should verify signatures before pulling. | MEDIUM — depends on operator following the hardening guide |
| **Disclosure** | Operator exposes Postgres or Redis directly to the internet | docker-compose binds DB ports to `127.0.0.1` only. Hardening guide documents firewall rules. | MEDIUM — operator responsibility |
| **Denial of Service** | Operator's deployment lacks TLS; traffic sniffed | nginx config ships with Let's Encrypt TLS instructions; app refuses to start in `PRODUCTION` mode without `HTTPS=true`. | LOW |
| **Elevation of Privilege** | Attacker gains access to the host and reads the `.env` file | Hardening guide recommends: no root docker socket mount, `--read-only` container filesystem where possible, `.env` file permissions 0600. | MEDIUM — operator responsibility |

---

## 5. Summary Findings and Action Table

| Finding | Severity | Mitigation | Owner Task | Status |
|---|---|---|---|---|
| SSRF from ingest worker fetching arbitrary recipe URLs (including cloud metadata endpoint) | HIGH | URL/IP blocklist with DNS rebinding check; recipes require human approval | D1 | IN_PROGRESS |
| Prompt injection via adversarial scraped content into LLM extraction calls | MEDIUM | Output schema validation; single-workspace contexts; prompt version rotation | E2 | IN_PROGRESS |
| Workspace isolation bypass (cross-workspace data leak) | CRITICAL | SQLAlchemy event listener; integration tests; pentest (R2) | B7, B8, R2 | NOT_STARTED |
| OAuth tokens for CRM integrations stored in Postgres without per-token encryption | HIGH | Encrypt OAuth tokens at rest (field-level encryption); audit in K1 | K1 | NOT_STARTED |
| Webhook URL SSRF (customer-configured webhook pointing to internal services) | MEDIUM | Validate webhook URLs against SSRF blocklist at registration time | L3 | NOT_STARTED |
| Webhook replay / forgery (outbound webhooks lack HMAC signature) | MEDIUM | HMAC-SHA256 signing per L3 spec; 5-minute replay window | L3 | NOT_STARTED |
| API token brute force (no application-layer rate limiting on auth endpoints in Phase 0) | MEDIUM | WAF rate limits (cloud); application-layer rate limiter (B1); account lockout | B1, A5 | NOT_STARTED |
| Self-host: operator may expose DB ports or skip TLS | MEDIUM | docker-compose binds DB to 127.0.0.1; hardening guide; `HTTPS` guard in app startup | O4, docs | NOT_STARTED |
| LLM provider receives workspace document content (privacy / data minimization) | MEDIUM | Gateway PII redaction; privacy policy disclosure; BYO key on Core | E2, LC-9 | IN_PROGRESS |
| Secrets management on self-host (plaintext .env files) | MEDIUM | Hardening guide; sealed-secrets guidance; operator responsibility | O4 | NOT_STARTED |
| Adversarial PDF / HTML stored in S3 and rendered in-app | MEDIUM | PDF.js sandboxed iframe with CSP; DOMPurify for HTML; no server-side execute | E1 | NOT_STARTED |
| Redis lacks authentication by default in development config | LOW | Production compose requires `REDIS_PASSWORD`; dev config is isolated; documented | A4, O4 | IN_PROGRESS |
| External pentest has not been performed | HIGH | Schedule R2 engagement after this document is reviewed | R2 | NOT_STARTED |

---

## 6. OWASP ASVS Level 2 Coverage Summary

Self-assessment against OWASP ASVS Level 2 (target baseline per NFRs §4.2). Full assessment maintained separately in `security/asvs.md` (TODO, companion to this document).

| ASVS Category | Coverage Status | Notes |
|---|---|---|
| V1 Architecture | Partial | Modulith boundaries documented; pentest scope (R2) pending |
| V2 Authentication | Partial | bcrypt(12), TOTP MFA, session management designed (B1–B4 pending) |
| V3 Session Management | Partial | Cookie attributes specified; implementation in B1 |
| V4 Access Control | Partial | RBAC model designed (B7); workspace isolation designed; not yet code-complete |
| V5 Validation / Sanitization | Not started | Pydantic v2 for inputs; HTML sanitization for stored content (E1) |
| V6 Cryptography | Partial | bcrypt for passwords; AES-256 at rest; TLS 1.2+; field-level encryption for OAuth tokens (K1) |
| V7 Error Handling / Logging | Partial | Structured logging; PII scrubbing rules designed; Sentry configured |
| V8 Data Protection | Partial | Workspace scoping; data classification above; retention policies (NFR §6) |
| V9 Communications | Low | TLS everywhere specified; mTLS for internal services deferred |
| V10 Malicious Code | Low | Supply-chain: Dependabot + Trivy; gitleaks; SBOM per release |
| V11 Business Logic | Not started | Rate limits, quota enforcement (N3, N4) |
| V13 API | Partial | OpenAPI contract tests (QA-2); schemathesis; RFC 7807 errors |
| V14 Configuration | Not started | Hardening guide (O4); Docker image minimization |

---

## 7. Scope for R2 Pentest

The following areas should be explicitly in scope for the R2 external penetration test:

1. **Multi-tenant isolation:** Attempt workspace data leakage through all API endpoints; fuzzing of `workspace_id` in headers and request bodies.
2. **Authentication:** Brute force resistance, session fixation, cookie theft vectors, MFA bypass.
3. **Ingestion SSRF:** Craft recipes or manipulate the ingest worker to reach RFC 1918 addresses or cloud metadata endpoints.
4. **Prompt injection:** Deliver adversarial content through scraped documents to corrupt extracted signals or leak prompt/context data.
5. **Webhook SSRF and HMAC bypass:** Register webhook URLs pointing to internal services; replay or forge signed webhook deliveries.
6. **OAuth token handling:** Attempt to extract or use another workspace's CRM OAuth tokens.
7. **API token scope escalation:** Use a narrow-scope token to reach endpoints requiring broader scope.
8. **Self-host image supply chain:** Verify cosign signatures; check SBOM completeness.

Findings from R2 will be triaged against this document, and open items will be tracked in GitHub Security Advisories.

---

## 8. Related Documents

- `SECURITY.md` — public vulnerability disclosure policy and security contact (LC-10 scope)
- `docs/legal/security.md` — public-facing security page summarizing practices
- `security/asvs.md` — OWASP ASVS Level 2 self-assessment (companion, TODO)
- `docs/self-host/hardening.md` — self-host operator hardening guide (O4 scope)
- NFRs doc §4 — security non-functional requirements
- Architecture doc §11 — security assumptions
