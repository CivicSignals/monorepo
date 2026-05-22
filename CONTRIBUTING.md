# Contributing to CivicSignals

Thanks for your interest in contributing! CivicSignals is an
**AGPL-3.0-only** open-source project, and we welcome contributions of code,
recipes, documentation, and bug reports.

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- **Code** — fix bugs or implement features (see open issues and the roadmap).
- **Recipes** — add or maintain ingestion recipes for new public-data sources.
  Recipes are contributed as YAML pull requests with golden fixtures (no UI for
  recipe authoring at this stage). See the recipe authoring guide in the docs.
- **Docs** — improve product, API, or self-hosting documentation.
- **Triage** — reproduce bugs, suggest fixes, review PRs.

## Project layout

This is a **Turborepo** monorepo with two language ecosystems:

- `apps/web` — Next.js 15 + React 19, TypeScript, Tailwind, shadcn/ui.
- `apps/api` — FastAPI modulith + Celery workers, Python 3.12 (managed by `uv`).
- `packages/sdk-ts`, `packages/sdk-py` — REST client SDKs.
- `packages/recipe-schema` — JSON Schemas that are the single source of truth
  for recipes and connectors.
- `recipes/` — declarative source recipes + golden fixtures.
- `infra/` — docker-compose stacks and deployment scripts.
- `docs/` — documentation sources.

The API is a **modulith**: each module under
`apps/api/src/civicsignals_api/modules/<name>/` exposes `services.py`,
`models.py`, `schemas.py`, `routes.py`, `tasks.py`, and `tests/`. No module
imports another module's internals — cross-module calls go through the target's
`services.py`. Each module owns its own tables (prefixed `<module>_`).

## Development setup

Prerequisites: **Node 22** (`.nvmrc`) with **pnpm 9** (`corepack enable`),
**Python 3.12** (`.python-version`) with **[uv](https://docs.astral.sh/uv/)**,
and Docker.

```bash
pnpm install                 # Node workspaces
cd apps/api && uv sync       # Python deps for the API
make dev                     # full local stack (Postgres+pgvector, Redis, MinIO,
                             # Mailpit, PgBouncer, api, workers, scheduler, web)
make migrate                 # apply Alembic migrations
make seed                    # seed a demo workspace
```

## Coding conventions

**Python (`apps/api`, `packages/sdk-py`)**

- `from __future__ import annotations` at the top of every module.
- `ruff` for lint **and** format (line length 100). Lint with
  `uv run ruff check .` (add `--fix` to auto-fix) and format with
  `uv run ruff format .` (use `--check` in CI to verify formatting).
- `mypy --strict`: `uv run mypy src`
- Tests with `pytest` (asyncio auto-mode): `uv run pytest`

**TypeScript (`apps/web`, `packages/sdk-ts`)**

- ESLint + Prettier: `pnpm lint`, `pnpm format`
- Type-check: `pnpm typecheck`
- Tests with Vitest: `pnpm --filter @civicsignals/web test`

Run everything across the monorepo with `pnpm lint`, `pnpm typecheck`,
`pnpm test`. CI runs all of these on every PR and they must pass.

## Pull request process

1. Fork (or branch, if you have write access) from `main`.
2. Make focused changes with clear commit messages.
3. Ensure lint, type-check, and tests pass locally.
4. **Sign off your commits** (see DCO below).
5. Open a PR using the template. Describe what changed, why, how you verified
   it, and link the relevant issue or task ID.
6. A maintainer (and our automated reviewer) will review. Address feedback and
   keep the branch up to date with `main`.

Keep PRs small and single-purpose where possible — they are easier to review
and merge.

## Developer Certificate of Origin (DCO)

CivicSignals uses the **Developer Certificate of Origin** instead of a CLA. The
DCO is a lightweight way for you to certify that you wrote, or otherwise have
the right to submit, the code you are contributing. Read the full text at
<https://developercertificate.org/>.

To sign off, add a `Signed-off-by` line to each commit — `git` does this for you
with the `-s` flag:

```bash
git commit -s -m "Your commit message"
```

This appends:

```
Signed-off-by: Your Name <your.email@example.com>
```

The name and email must match your Git author identity. **Pull requests whose
commits are not signed off cannot be merged.** If you forget, you can amend
with `git commit --amend -s` (or rebase with `git rebase --signoff` for multiple
commits) and force-push.

## Licensing

By contributing, you agree that your contributions are licensed under the
project's **AGPL-3.0-only** license. See [LICENSE](LICENSE) for the full text;
a plain-language License FAQ is published under `docs/legal/` (`license-faq.md`)
explaining what AGPL-3.0 means in practice for self-hosters and SaaS use.

## Reporting security issues

Please **do not** open public issues for security vulnerabilities. Follow
[SECURITY.md](SECURITY.md) instead.
