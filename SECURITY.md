# Security Policy

CivicSignals takes the security of its software and the data entrusted to it
seriously. Thank you for helping keep CivicSignals and its users safe.

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
discussions, or pull requests.**

Instead, email **security@civicsignals.io** with:

- A description of the issue and its potential impact.
- Steps to reproduce (proof-of-concept, affected endpoints/components, version
  or commit SHA).
- Any relevant logs, screenshots, or configuration (with secrets redacted).

If you would like to encrypt your report, request our PGP key in an initial
(content-free) email and we will respond with it.

You should receive an acknowledgement within **3 business days**. If you do not,
please follow up to ensure we received your original message.

## Coordinated disclosure timeline

We follow a coordinated disclosure process:

| Stage | Target |
|-------|--------|
| Acknowledgement of report | within 3 business days |
| Initial assessment & severity triage | within 7 days |
| Fix or mitigation for High/Critical issues | within 30 days |
| Public disclosure / advisory | after a fix ships, coordinated with the reporter |

We will keep you informed throughout, credit you in the advisory (unless you
prefer to remain anonymous), and aim to publish a GitHub Security Advisory for
confirmed vulnerabilities.

## Supported versions

CivicSignals is pre-1.0. During this phase, **only the latest released version
and `main`** receive security fixes. Once we reach 1.0, this table will track
supported release lines.

| Version | Supported |
|---------|-----------|
| `main` / latest release | ✅ |
| older pre-1.0 tags | ❌ |

## Scope

In scope:

- The CivicSignals API (`apps/api`), web app (`apps/web`), workers, and the
  official Docker images / Helm chart.
- The recipe runner and connector framework (handling of untrusted, scraped web
  content).
- Authentication, multi-tenant workspace isolation, API tokens, and integration
  credential handling.

Out of scope:

- Findings that require physical access to a user's device.
- Social-engineering of CivicSignals staff or users.
- Volumetric denial-of-service.
- Vulnerabilities in third-party dependencies that are already publicly known
  and have an upstream fix pending (please still let us know).
- Self-hosted instances misconfigured contrary to our hardening documentation.

## Hardening & operational guidance

Self-hosters should follow the production and hardening guides under
`docs/` (see also the public security page in `docs/legal/security.md`) and the
project threat model at `security/threat-model.md`.

## Safe harbor

We will not pursue legal action against researchers who act in good faith,
follow this policy, avoid privacy violations and service degradation, and give
us reasonable time to remediate before any disclosure.
