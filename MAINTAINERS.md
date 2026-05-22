# Maintainers

This file lists the people responsible for maintaining CivicSignals, their
areas of ownership, and how project decisions are made.

## Current maintainers

| Maintainer | GitHub | Areas |
|------------|--------|-------|
| _Cofounder 1 (placeholder)_ | `@maintainer1` | Backend (API modulith, ingestion, signal processing), infrastructure |
| _Cofounder 2 (placeholder)_ | `@maintainer2` | Frontend (web app), product, integrations, docs |

> These are placeholders for the two founding maintainers and will be filled in
> before the public OSS release (LC-16).

To reach the maintainers privately: **maintainers@civicsignals.io**. For
security reports, use **security@civicsignals.io** (see [SECURITY.md](SECURITY.md)).

## Areas of ownership

Ownership maps to the delivery-plan epics and the API modules. The list below is
a guide for routing reviews, not a hard gate. Module paths are relative to
`apps/api/src/civicsignals_api/` (e.g. `modules/auth` →
`apps/api/src/civicsignals_api/modules/auth`, `llm_gateway.py` →
`apps/api/src/civicsignals_api/llm_gateway.py`); `prompts/` is `apps/api/prompts/`,
the web app is `apps/web`, and the docs site is `docs-site/`.

| Area | Modules / paths | Primary owner |
|------|-----------------|---------------|
| Platform & infra | `infra/`, CI workflows, Docker/Helm | Cofounder 1 |
| Auth & accounts | `modules/auth`, `modules/accounts` | Cofounder 1 |
| Entities & contacts | `modules/entities`, `modules/contacts` | shared |
| Ingestion & recipes | `modules/ingestion`, `modules/recipes`, `packages/recipe-schema`, `recipes/` | Cofounder 1 |
| Extraction & signals | `modules/extraction`, `modules/signals`, `llm_gateway.py`, `prompts/` | Cofounder 1 |
| ICP, search & feed | `modules/icp`, `modules/searches`, `modules/smart_search` | shared |
| Integrations & FOIA | `modules/integrations`, `modules/foia`, `modules/pipeline` | Cofounder 2 |
| Billing & admin | `modules/billing`, `modules/admin` | Cofounder 2 |
| Web app | `apps/web` | Cofounder 2 |
| SDKs | `packages/sdk-ts`, `packages/sdk-py` | shared |
| Docs | `docs/`, `docs-site/` | Cofounder 2 |

## Becoming a maintainer

Contributors who demonstrate sustained, high-quality contributions and good
judgment in reviews may be invited to become maintainers by consensus of the
existing maintainers.

## Decision making

- **Day-to-day changes** are made by PR with at least one maintainer approval
  (and passing CI). Our automated reviewer comments on every PR; a human
  maintainer makes the merge decision.
- **Significant changes** (architecture, data model, public API, license, or
  governance) are discussed in an issue or RFC and require consensus of the
  maintainers.
- **Disagreements** are resolved by discussion aiming for consensus. If
  consensus cannot be reached, a simple majority of maintainers decides; ties
  defer to the area owner.

## Responsibilities

Maintainers are expected to review PRs in their areas in a timely manner,
uphold the [Code of Conduct](CODE_OF_CONDUCT.md), follow the security policy,
and keep the roadmap and documentation honest.
