// QA-1 — Playwright configuration for CivicSignals web E2E harness.
// Spec doc: 11-qa-test-plan.md §2 (CUF-1..CUF-9)
//
// Smoke subset: runs on every PR against a locally-started Next.js dev server.
// Full suite:   runs nightly; fixme/skip tests are run with --grep skipping
//               fixme tests (they're automatically skipped by Playwright).
//
// QA-5: Browser matrix — three desktop engine projects (Chromium, Firefox,
// WebKit/Safari). All existing specs are browser-agnostic and run unchanged on
// every engine. If a future spec targets Chromium-only behaviour, gate it with
// `test.skip(({ browserName }) => browserName !== 'chromium', 'Chromium only')`
// inside the spec body, or use `testInfo.project.name` for project-name checks,
// or add `grep`/`grepInvert` to the relevant project entry below.
//
// Mobile-viewport sanity: NOT automated here (QA-5 scope decision). To run
// manually, add a mobile project (e.g. `use: { ...devices['Pixel 5'] }`) to
// this config locally and run `playwright test --project=<mobile-project-name>`
// before any release that changes responsive layout.
import { defineConfig, devices } from "@playwright/test";

// The base URL is overridable via env so CI can point at a pre-built `next start`
// process or a remotely deployed staging URL without touching this file.
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";

// QA-C: full-stack e2e specs are tagged @fullstack. They need a SEEDED, running
// stack (web + api + DB + workers), so they must NOT run in the smoke / full /
// browser projects below (those start only `next dev` and have no backend) —
// every non-fullstack project carries this grepInvert. The dedicated
// `full-stack` project (grep: /@fullstack/) runs them against an
// externally-started, seeded stack and is the ONLY project that runs them.
const FULLSTACK_TAG = /@fullstack/;

export default defineConfig({
  testDir: "./e2e",
  // Match only .spec.ts files in the e2e directory.
  testMatch: "**/*.spec.ts",
  // Give each test enough room; pages are not data-heavy yet.
  timeout: 30_000,
  // Expect assertions get a tighter timeout than page navigations.
  expect: { timeout: 10_000 },

  // Retry flaky tests once in CI; locally don't retry so failures surface fast.
  retries: process.env.CI ? 1 : 0,

  // Capture traces on the first retry (trace-on-failure).
  use: {
    baseURL: BASE_URL,
    // Full page screenshot on failure.
    screenshot: "only-on-failure",
    // Trace on the first retry for debugging.
    trace: "on-first-retry",
    // Video on the first retry for debugging.
    video: "on-first-retry",
    // Increase navigation timeout for slow CI runners.
    navigationTimeout: 20_000,
    actionTimeout: 10_000,
  },

  // Reporter: list in CI (readable in GH Actions log), HTML for local dev.
  reporter: process.env.CI
    ? [["list"], ["github"]]
    : [["list"], ["html", { open: "never" }]],

  // --- Projects ---------------------------------------------------------------
  // QA-5: Three desktop engine projects — chromium, firefox, webkit.
  // Each engine has two variants: smoke (grep: @smoke, PR checks) and full
  // (all specs, nightly). The smoke projects are the CI default via `pnpm e2e:smoke`.
  //
  // WebKit system dependencies may not be available on every Linux runner (see
  // https://playwright.dev/docs/browsers#webkit). If webkit deps are missing in
  // CI the job is allowed to fail because e2e-smoke has `continue-on-error: true`.
  projects: [
    // ---- Chromium (Desktop Chrome) -----------------------------------------
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      // @fullstack specs need a seeded backend — they only run in `full-stack`.
      grepInvert: FULLSTACK_TAG,
    },
    {
      name: "chromium-smoke",
      use: { ...devices["Desktop Chrome"] },
      // Only smoke-tagged tests run in this project (tag: @smoke). @smoke and
      // @fullstack are disjoint, so smoke can never pick up a full-stack spec,
      // but the grepInvert makes that invariant explicit and future-proof.
      grep: /@smoke/,
      grepInvert: FULLSTACK_TAG,
    },
    {
      name: "chromium-full",
      use: { ...devices["Desktop Chrome"] },
      // Full project runs all non-fullstack tests; fixme tests are auto-skipped
      // by Playwright. @fullstack is excluded — it has no backend here.
      grepInvert: FULLSTACK_TAG,
    },

    // ---- Firefox (Desktop Firefox) -----------------------------------------
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
      grepInvert: FULLSTACK_TAG,
    },
    {
      name: "firefox-smoke",
      use: { ...devices["Desktop Firefox"] },
      grep: /@smoke/,
      grepInvert: FULLSTACK_TAG,
    },
    {
      name: "firefox-full",
      use: { ...devices["Desktop Firefox"] },
      grepInvert: FULLSTACK_TAG,
    },

    // ---- WebKit / Safari engine (Desktop Safari) ---------------------------
    // WebKit binary installs reliably on Linux CI; however OS-level deps
    // (libwoff2, libflite, libhyphen, etc.) may be missing on minimal runners.
    // Keep the projects here so the matrix is complete; rely on
    // `continue-on-error: true` in e2e-smoke if webkit fails in CI.
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
      grepInvert: FULLSTACK_TAG,
    },
    {
      name: "webkit-smoke",
      use: { ...devices["Desktop Safari"] },
      grep: /@smoke/,
      grepInvert: FULLSTACK_TAG,
    },
    {
      name: "webkit-full",
      use: { ...devices["Desktop Safari"] },
      grepInvert: FULLSTACK_TAG,
    },

    // ---- Full-stack (seeded backend required) ------------------------------
    // The ONLY project that runs @fullstack specs (feed-routing, CUF-1). It
    // expects an externally-started, seeded stack: run with
    // PLAYWRIGHT_NO_WEBSERVER=1 and PLAYWRIGHT_BASE_URL pointing at the running
    // web app (the `webServer` block below is disabled by NO_WEBSERVER). CI
    // brings the stack up + seeds it (`seed_e2e`) before invoking this project.
    // Single engine (Chromium) — these assert backend/data behaviour, not
    // cross-browser rendering, so the matrix would only add cost.
    {
      name: "full-stack",
      use: { ...devices["Desktop Chrome"] },
      grep: FULLSTACK_TAG,
    },
  ],

  // --- Web server -------------------------------------------------------------
  // Automatically start `next dev` before running tests and kill it after.
  // In CI the build job produces an artifact; the smoke job starts `next start`
  // with the built output (see .github/workflows/ci.yml e2e-smoke job).
  // Locally `pnpm e2e` spins up the dev server on the fly.
  webServer: process.env.PLAYWRIGHT_NO_WEBSERVER
    ? undefined
    : {
        command: process.env.PLAYWRIGHT_START_CMD ?? "pnpm dev",
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        // Increase startup timeout so `next build && next start` has time.
        timeout: 120_000,
        stdout: "pipe",
        stderr: "pipe",
      },
});
