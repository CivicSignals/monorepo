# Security at CivicSignals

> **DRAFT — Pending security review. Not final until reviewed and published.**  
> **Last updated:** [DATE TO BE SET AT PUBLICATION]

This page summarizes CivicSignals's security practices for Cloud customers, self-hosters, and security researchers. It is the public-facing companion to our internal [threat model](../../security/threat-model.md) (engineering reference) and the repository `SECURITY.md` (vulnerability disclosure process for the open-source project — see task LC-10).

---

## Our Security Philosophy

CivicSignals handles public-sector procurement intelligence on behalf of sales teams and civic-tech researchers. While the underlying data is public (government websites), the intelligence layer — who is tracking what, with what ICP, in what pipeline — is confidential and business-sensitive.

We treat security as a first-class engineering concern, not an afterthought. Key principles:

1. **Workspace isolation is non-negotiable.** Your data is never accessible to another tenant. We enforce this at the ORM layer and test it explicitly.
2. **Defense in depth.** We use multiple overlapping controls rather than relying on any single layer.
3. **Transparency.** We publish our threat model, ASVS self-assessment, and this page. We'd rather you know what we've thought about than discover it in an audit.
4. **Open source as a forcing function.** The platform core is AGPL-3.0 open-source. Security researchers can read the code, not just our marketing copy.

---

## Key Security Controls

### Authentication and Access

- **Password hashing:** bcrypt with cost factor 12.
- **Multi-factor authentication (TOTP):** Available on all paid plans. Required for workspace administrators. Required for all staff with production access.
- **Session management:** Short-lived sessions (24-hour sliding, 30-day absolute). Sessions invalidated on password change, MFA enable/disable, and admin role changes.
- **API tokens:** Workspace-scoped, hashed at rest (revealed only once at creation), per-token scope controls (`signals:read`, `signals:write`, `crm:push`, etc.).
- **SSO/SAML:** Planned for Enterprise (v2).

### Network and Transport

- **TLS 1.2+ everywhere.** No unencrypted connections on the public surface. HSTS preloaded.
- **AWS WAF** (cloud): OWASP Top 10 managed rule sets, IP reputation filtering, rate limits on auth and API endpoints.
- **Rate limiting:** Per-IP and per-account limits on authentication endpoints; per-workspace limits on API and AI feature usage.
- **CSP (Content Security Policy):** Strict CSP with nonces on inline scripts; no `unsafe-inline`.

### Data Protection

- **Encryption at rest:** AES-256 for all Postgres volumes, S3 buckets, and cache snapshots.
- **Multi-tenant isolation:** Every database query on tenant-scoped data is enforced by a SQLAlchemy event listener that injects `WHERE workspace_id = :workspace_id`. Tests fail the build if a workspace-scoped query runs without the context set.
- **No cross-workspace tokens:** API tokens are bound to exactly one workspace with no cross-workspace capability.
- **Secrets management:** AWS Secrets Manager (cloud); environment variables with sealed-secrets guidance (self-host). 90-day rotation cycle for shared infrastructure secrets.

### Supply Chain and Dependencies

- **Signed container images:** All production images are signed with [cosign](https://github.com/sigstore/cosign). Verify with `cosign verify ghcr.io/civicsignals/api:VERSION`.
- **SBOM:** A Software Bill of Materials (SPDX + CycloneDX) is attached to every release.
- **Dependency scanning:** Dependabot for automated updates; Trivy in CI; critical CVEs block merge.
- **Secrets scanning:** gitleaks pre-commit hook and CI scan; no secrets may be committed.

### Application Security

- **OWASP ASVS Level 2** is our target baseline. A self-assessment is maintained in `security/asvs.md` and reviewed quarterly.
- **CSRF protection:** Double-submit cookie for cookie-authenticated state-changing requests.
- **SRI (Subresource Integrity):** Applied to third-party assets.
- **Injection prevention:** Parameterized queries throughout; Pydantic v2 for input validation; no raw string interpolation in queries.
- **Ingestion SSRF protection:** The scraper/ingest worker validates all URLs against an IP allowlist/blocklist (RFC 1918 ranges, loopback, cloud metadata endpoints) before fetching, including after redirects.
- **LLM prompt injection:** All LLM extraction calls use versioned, structured prompts. Output is validated against a strict JSON schema; non-conforming output is rejected.

### Logging and Monitoring

- **Structured logging:** All requests carry `request_id`, `workspace_id`, `user_id`. We do not log request bodies containing Customer Data at INFO level.
- **Audit log:** All authenticated actions are written to an append-only audit log (B9). Retention varies by plan (90 days to 7 years).
- **Error tracking:** Sentry with PII scrubbing rules (no request bodies, no Authorization headers).
- **Synthetic monitoring:** Every 5 minutes in production.

### Penetration Testing

- **Annual external penetration test** from month 6 after Cloud GA (task R2). Findings remediated or accepted with documented rationale.
- **Internal OWASP review** on each quarter.
- Continuous SAST/DAST tooling in CI.

---

## Compliance Status

| Framework | Status |
|---|---|
| OWASP ASVS Level 2 | Self-assessment in progress; full assessment before launch |
| SOC 2 Type I | In progress — auditor to be selected by month 3 post-GA |
| SOC 2 Type II | Targeted for month 18 post-GA |
| GDPR / CCPA | Compliance controls in place; see [Privacy Policy](privacy-policy.md) and [DPA](dpa.md) |
| FERPA | FERPA-alignment statement available on request (we do not process student records) |
| HIPAA | Out of scope — we do not accept BAAs or process PHI |
| FedRAMP | Not planned |

---

## Self-Hosted Security

Self-hosted CivicSignals Core operators own their security posture. We commit to:

- **Signed images** and **SBOM** per release (verify before pulling).
- **Security advisories** for the open-source code on the same SLA as Cloud.
- **Hardening guide** at `docs/self-host/hardening.md` covering: firewall rules, TLS configuration, secrets management (sealed-secrets / Vault), DB port binding, container best practices.
- **Safe defaults:** docker-compose binds database ports to `127.0.0.1`; the app requires `HTTPS=true` in production mode.

Operators are responsible for: network configuration, secrets management, backup procedures, and access controls on their own infrastructure.

---

## Vulnerability Disclosure

We operate a responsible disclosure program. If you discover a security vulnerability in CivicSignals (Cloud or open-source):

1. **Email:** security@civicsignals.io (PGP key published on the key servers and at `civicsignals.io/security.asc`)
2. **GitHub Security Advisories:** For the open-source project at `github.com/CivicSignals/civicsignals/security/advisories`
3. **Please do not** open a public GitHub issue for security vulnerabilities.

**Response SLA:**

| Severity | Acknowledgment | Patch and ship |
|---|---|---|
| Critical | 24 hours | 7 days |
| High | 3 business days | 30 days |
| Medium | 7 business days | 90 days |

Public disclosure is coordinated with the reporter. Default embargo: 90 days, negotiable. We will publicly acknowledge researchers who report qualifying vulnerabilities.

For full disclosure instructions, see [SECURITY.md](../../SECURITY.md) in the repository.

---

## Contact

- **Security vulnerabilities:** security@civicsignals.io
- **Privacy concerns:** privacy@civicsignals.io
- **General trust questions:** trust@civicsignals.io

**CivicSignals, Inc.**  
civicsignals.io
