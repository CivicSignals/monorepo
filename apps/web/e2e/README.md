# Web e2e (Playwright)

Browser-driven end-to-end tests for the CivicSignals web app. There are three
kinds of spec here, distinguished by tag, and they run in different places:

| Tag          | Needs a backend? | Project(s)            | When it runs              |
| ------------ | ---------------- | --------------------- | ------------------------- |
| `@smoke`     | No (static UI)   | `*-smoke`             | Every PR                  |
| (untagged)   | No (`next dev`)  | `chromium/firefox/webkit`, `*-full` | Nightly full suite |
| `@fullstack` | **Yes** (seeded) | `full-stack` **only** | Nightly / on demand in CI |

The full rules (determinism, seam discipline, selector policy, how to add a
source or scenario) live in [`docs/testing/e2e-rules.md`](../../../docs/testing/e2e-rules.md).
This file is the quick local reference.

## Layout

```
e2e/
  smoke.spec.ts            # @smoke — static marketing surface (runs on every PR)
  feed-routing.spec.ts     # @fullstack — per-workspace signal routing (headline)
  cuf-1-signup.spec.ts     # @fullstack — signup → workspace → empty feed (+ fixmes)
  cuf-2..9-*.spec.ts       # scaffolds (test.fixme) for the other critical flows
  helpers/
    users.ts               # seeded users + expected signal titles (mirrors scenarios.yaml)
    auth.ts                # loginAs(): drives the real login page; storageState reuse
    feed.ts                # FeedPage / SignalDetailPage page-objects (data-testid only)
  .auth/                   # per-run storageState cache (gitignored; holds tokens)
```

## Running

```bash
# Smoke (PR check) — starts `next dev` automatically, no backend needed.
pnpm --filter @civicsignals/web e2e:smoke

# Full non-fullstack suite (nightly) — also `next dev`, single engine.
pnpm --filter @civicsignals/web e2e

# Collect/compile every spec WITHOUT running a browser (CI gate + quick sanity):
pnpm --filter @civicsignals/web exec playwright test --list
```

### Full-stack specs (`@fullstack`)

These need a **running, seeded** stack and only run in the `full-stack` project.
They expect the stack to be started **externally** (CI brings it up), so point
Playwright at it and disable the built-in `next dev` web server:

```bash
# 1. Bring the stack up (Postgres+pgvector, Redis, MinIO, api, workers, web):
make dev            # repo root

# 2. Seed the deterministic e2e scenarios (real extraction pipeline, no network):
cd apps/api && uv run python -m civicsignals_api.scripts.seed_e2e

# 3. Run the full-stack project against the already-running web app:
PLAYWRIGHT_NO_WEBSERVER=1 \
PLAYWRIGHT_BASE_URL=http://localhost:3000 \
  pnpm --filter @civicsignals/web e2e:fullstack
```

`PLAYWRIGHT_NO_WEBSERVER` disables the config's auto-start `webServer` (so
Playwright does not try to boot its own `next dev`); `PLAYWRIGHT_BASE_URL`
points it at the running web app. The seed step is idempotent — re-running it
produces the same rows, so reruns are safe.

## Conventions (enforced)

- **Selectors are `data-testid` only.** No CSS classes, no brittle text-position
  selectors. Accessible-role/label queries are acceptable for genuinely semantic
  controls (e.g. the workspace `<select>` labelled "Active workspace").
- **No real network / LLM.** The backend the full-stack specs hit is seeded by
  the deterministic fixture LLM gateway (`LLM_BACKEND=fake`); specs never reach
  out to vendors. UI specs that don't need a backend run against static pages.
- **Per-run uniqueness.** Anything that writes (e.g. CUF-1 signup) embeds a
  unique per-run id so reruns never collide on the real DB.
- **Assert against the manifest.** Seeded credentials and expected signal titles
  come from `helpers/users.ts`, the TS mirror of
  `apps/api/tests/e2e_fixtures/scenarios.yaml` and the golden `expected-signal.json`
  fixtures. Keep the two in sync (see the rules doc).

## Adding `data-testid`s

Add them on the element a spec needs to find, with a stable, descriptive name in
the existing kebab-case convention (`feed-item`, `login-submit`, `why-bullet`).
Prefer reusing an existing testid over adding a near-duplicate. If you add one in
a `.tsx`, run that component's unit tests (`pnpm --filter @civicsignals/web test`).
