# E2E testing rules

The contract for CivicSignals' end-to-end tests: how they stay deterministic,
where each layer runs, and the recipes for the two changes people make most
often — adding a new source, and adding a new "user waits for a signal type"
scenario. Read this before touching the e2e harness.

## The three layers

E2E coverage is split into three layers, each owning a different slice of the
pipeline so a failure points at the right place:

| Layer | What it proves | Where it lives | Runtime | CI |
| ----- | -------------- | -------------- | ------- | -- |
| **A — connector / extraction** | One source type's `discover → fetch → extract → normalize` against golden fixtures (selectors, fallbacks, dead-letter). | `apps/api/.../modules/ingestion/tests/test_connector_integration.py`; recipe goldens under `recipes/<id>/fixtures/`. | Fast (pytest, no network) | Every PR |
| **B — source → signal analytics** | A stored raw document runs the **real** extraction pipeline to a `signals_signal` row, and per-workspace scoring routes it correctly. | `apps/api/.../modules/{extraction,signals}/tests/`. | Fast (pytest, fake LLM) | Every PR |
| **C — full-stack browser** | A real user logs in and sees exactly the signals their workspace should — the seam from DB rows to rendered feed. | `apps/web/e2e/feed-routing.spec.ts`, `cuf-*.spec.ts` (`@fullstack`). | Slow (Playwright + seeded stack) | Nightly / on demand |

Layers A and B run in the Python test suite on every PR (`pnpm test` /
`uv run pytest`). Layer C runs against a seeded, running stack and is the only
layer that needs a browser and a database — so it runs nightly (or on demand),
not on every PR.

Playwright specs themselves carry a finer split by tag:

- `@smoke` — static UI that exists today (landing/pricing/404). Runs on **every
  PR** in the `chromium-smoke` project against `next dev` (no backend).
- untagged — the broader UI suite; nightly `*-full` projects, still backend-less.
- `@fullstack` — needs the seeded stack; runs **only** in the `full-stack`
  project. The smoke/full/browser projects all carry `grepInvert: /@fullstack/`
  so a full-stack spec can never run without a backend (it would otherwise hit
  the auth-required empty state and fail confusingly).

## Determinism rules

E2E tests must be reproducible bit-for-bit; flakiness erodes trust faster than a
missing test. The rules:

1. **No real network, no real LLM.** The full-stack stack is seeded by
   `civicsignals_api.scripts.seed_e2e`, which runs the production extraction
   pipeline with `LLM_BACKEND=fake` and a per-scenario `llm-responses.json`
   script (relevance / entity / signal). No vendor SDK is ever called; no HTTP
   egress happens. Connector tests replay golden `*.html` fixtures, never live
   sites.
2. **Seeding is idempotent.** Entities upsert on their external identifier;
   users/workspaces/ICPs get-or-create on stable email/slug; raw documents and
   signals dedupe on content hash. Re-running `seed_e2e` yields the same rows —
   so a rerun (or a retry) is safe and produces the same assertions.
3. **Unique-per-run ids for anything a test creates.** A spec that writes (e.g.
   CUF-1 signup) embeds `Date.now()` (+ random) in the email / workspace name so
   reruns don't collide on the real DB. Read-only specs assert against the fixed
   seeded identities instead.
4. **Wait on state, not on time.** No `sleep`/fixed delays. Page-objects wait for
   a terminal `data-testid` (feed list / empty / auth-required) before
   asserting. Playwright auto-waits on locators; lean on it.
5. **One source of truth, mirrored loudly.** Expected signal titles + seeded
   credentials live in `scenarios.yaml` + the golden `expected-signal.json`
   fixtures (backend) and in `apps/web/e2e/helpers/users.ts` (frontend). They are
   duplicated on purpose — a drift between seed and assertion fails a test rather
   than silently passing. Change both together.

## Seam discipline

The pieces that make determinism possible are seams — swap the real
implementation for a deterministic one at the boundary, never inside business
logic:

- **LLM gateway (`llm_gateway.py`).** No module calls a vendor SDK directly; all
  model access goes through the gateway. Tests build a *fake / fixture-backed*
  gateway (`build_fake_gateway`) that returns the scripted `llm-responses.json`.
  This is the single seam that makes extraction deterministic.
- **Fetcher / content store.** Connectors fetch through an injectable fetcher and
  store via `ingestion.services.store_raw_document` against an **in-memory**
  content store in tests (no S3/MinIO, no HTTP). Golden HTML fixtures are the
  inputs.
- **Cross-module calls go through `services.py`.** Tests and the seeder call a
  module's public service functions (`accounts`, `icp`, `signals`,
  `extraction.pipeline`) — never another module's internals (doc 06 §3). This
  keeps the seeder exercising the same code paths production does.
- **Selectors: `data-testid` only.** The DOM is a seam too. Specs locate elements
  by `data-testid`, not CSS classes or text position, so a restyle never breaks a
  test and a testid rename is a deliberate, greppable change. Accessible
  role/label queries are fine for genuinely semantic controls (e.g. the
  "Active workspace" `<select>`). Add a testid on the element a spec needs, in the
  existing kebab-case convention (`feed-item`, `login-submit`, `why-bullet`); when
  you add one in a `.tsx`, run that component's unit tests.

## How to add a new source (connector)

A **connector** is code for a source *type*; a **recipe** is declarative YAML
instantiating it for one tenant/source. To add coverage for a new source:

1. **Author / reuse the connector** under
   `apps/api/.../modules/ingestion/connectors/`. Implement the
   `discover → fetch → extract → normalize` lifecycle; `extract` uses ordered
   selector fallbacks (primary → fallback → LLM-assisted → dead-letter), flagging
   `degraded: true` when it falls back.
2. **Add the recipe + golden fixtures** under `recipes/<id>/`. Each recipe ships
   `fixtures/<name>.html` (captured input) + `fixtures/<name>.expected.json`
   (the normalized output CI replays against). The recipe YAML must validate
   against the draft-07 schemas in `packages/recipe-schema` (the single source of
   truth for the Python runner and TS tooling).
3. **Add a Layer-A connector test** (or extend the parametrized
   `test_connector_integration.py`) that loads the golden fixture and asserts the
   normalized output equals `*.expected.json`, including the `degraded` flag on
   fallback paths. No network — the fixture is the input.
4. CI replays the goldens on every PR; a selector drift surfaces as a fixture
   mismatch, not a production incident.

You do **not** need a full-stack (Layer C) test for every source — Layer A/B
cover extraction correctness. Add a Layer C scenario only when a source unlocks a
new *user-visible routing* behaviour (next section).

## How to add a "user waits for signal type X" scenario (Layer C)

This is the routing invariant the full-stack suite proves: a user with a given
ICP sees exactly the signals that match it, and nothing else. To add one (say,
"Carol in NY waits for `grant_opportunity`"):

1. **Add a scenario folder** `apps/api/tests/e2e_fixtures/<scenario_id>/` with:
   - `source.html` — the mocked input, embedding a unique `marker` token so the
     fixture LLM backend routes the right scripted responses to this scenario.
   - `llm-responses.json` — the scripted relevance / entity / signal responses
     keyed by that marker.
   - `expected-signal.json` — the golden signal the pipeline must produce
     (`signal_type`, `title`, `summary`, entity state/type, details).
2. **Add a manifest entry** in `apps/api/tests/e2e_fixtures/scenarios.yaml`:
   - a `scenarios:` block (id, dir, marker, recipe_id, connector, source_url,
     `entity` to resolve, `expected_signal_type`); and, if the scenario
     introduces a new persona,
   - a `users:` block with the user's email/password/workspace + the `icp`
     (countries/states/entity_kinds/signal_types/threshold) and
     `expects_scenarios`. Choose the ICP so the routing is unambiguous: the
     scenario's signal must clear this user's threshold and miss every other
     user's ICP (use a deliberately out-of-ICP entity for "must be absent"
     cases, like `weak_match`'s VT/library_system).
3. **Mirror it in the frontend** `apps/web/e2e/helpers/users.ts`: add the
   `SeededUser` (email/password/workspace name + `expectedSignalTitle` =
   the golden `title`, `expectedSignalType`). Keep it identical to the manifest —
   they are intentionally duplicated so drift fails loudly.
4. **Add the Playwright assertion** in `apps/web/e2e/feed-routing.spec.ts` (or a
   sibling `@fullstack` spec): `loginAs(page, CAROL)`, then via the `FeedPage`
   page-object assert `expectSignalVisible(CAROL.expectedSignalTitle)` and
   `expectSignalAbsent(...)` for the other personas' signals — and open the
   detail page to assert the "Why this signal?" bullets explain the matched ICP
   axes (state / entity kind / signal type), which the scorer seeds as structured
   bullets the UI sentence-cases.
5. Re-run the seeder (idempotent) and the `full-stack` project locally to
   confirm the routing holds before pushing.

## Running the full-stack e2e locally

```bash
# 1. Bring the stack up from the repo root (Postgres+pgvector, Redis, MinIO,
#    api, workers, web). `make dev` builds + starts the dev compose stack.
make dev

# 2. Seed the deterministic routing scenarios — runs the real extraction
#    pipeline with the fake LLM gateway, no network, idempotent.
cd apps/api && uv run python -m civicsignals_api.scripts.seed_e2e
#    (or the container `seed-e2e` process type via docker-entrypoint.sh)

# 3. Run the full-stack Playwright project against the already-running web app.
#    NO_WEBSERVER disables Playwright's own `next dev`; BASE_URL points at the
#    running app.
PLAYWRIGHT_NO_WEBSERVER=1 \
PLAYWRIGHT_BASE_URL=http://localhost:3000 \
  pnpm --filter @civicsignals/web e2e:fullstack
```

In CI the same shape applies: a job brings the stack up, runs `seed_e2e`, then
invokes the `full-stack` project with `PLAYWRIGHT_NO_WEBSERVER=1` +
`PLAYWRIGHT_BASE_URL`. The PR-time gate is just `playwright test --list` (compiles
and collects every spec without running a browser) plus the smoke project; the
full-stack project runs on the nightly / on-demand pipeline.
