// QA-1 — Playwright configuration for CivicSignals web E2E harness.
// Spec doc: 11-qa-test-plan.md §2 (CUF-1..CUF-9)
//
// Smoke subset: runs on every PR against a locally-started Next.js dev server.
// Full suite:   runs nightly; fixme/skip tests are run with --grep skipping
//               fixme tests (they're automatically skipped by Playwright).
//
// QA-5 will extend this config with Firefox + Safari projects (browser matrix).
import { defineConfig, devices } from "@playwright/test";

// The base URL is overridable via env so CI can point at a pre-built `next start`
// process or a remotely deployed staging URL without touching this file.
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";

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
  // Smoke: Chromium only for PR checks (fast, reliable).
  // Full:  Firefox + WebKit added by QA-5 (browser matrix task).
  projects: [
    {
      name: "chromium-smoke",
      use: { ...devices["Desktop Chrome"] },
      // Only smoke-tagged tests run in this project (tag: @smoke).
      grep: /@smoke/,
      grepInvert: undefined,
    },
    {
      name: "chromium-full",
      use: { ...devices["Desktop Chrome"] },
      // Full project runs all tests; fixme tests are auto-skipped by Playwright.
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
