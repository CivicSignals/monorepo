// QA-1 — CUF-6: Self-host install
// Spec: 11-qa-test-plan.md §2 CUF-6, user journey: 04-user-journeys.md J8
//
// Status: SCAFFOLD — all tests are test.fixme() pending O1 (self-host packaging).
//
// This flow primarily lives in a separate CI job that spins up a fresh VM
// (see doc 11 §2 CUF-6: "runs nightly against a fresh ephemeral VM").
// The Playwright harness here validates the web UI side of the self-host flow
// once the docker-compose stack is up.
//
// TODO O1: self-host packaging + quickstart → enable these tests.
// TODO O3: Helm chart tests (kubernetes self-host variant).
// TODO B1: auth prerequisite (bootstrap admin creation).
// TODO D1: recipe runner prerequisite (add built-in test recipe + trigger ingestion).

import { test, expect } from "@playwright/test";

test.describe("CUF-6: Self-host install (web UI side)", () => {
  test.fixme(
    "Step 3: self-hosted instance responds at the configured URL",
    async ({ page }) => {
      // TODO O1: the nightly CI job starts a fresh compose stack; Playwright
      // is pointed at it via PLAYWRIGHT_BASE_URL.
      //
      // const response = await page.goto('/');
      // expect(response?.status()).toBe(200);
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Step 4: bootstrap admin can sign in",
    async ({ page }) => {
      // TODO B1 + O1:
      // page.getByLabel('Email').fill('admin@example.com');
      // page.getByLabel('Password').fill(process.env.E2E_ADMIN_PASSWORD ?? 'admin');
      // page.getByRole('button', { name: /sign in/i }).click();
      // await expect(page).toHaveURL(/dashboard/i);
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Step 5: admin can create a workspace",
    async ({ page }) => {
      // TODO B5:
      // page.getByRole('button', { name: /create workspace/i }).click();
      // page.getByLabel('Company name').fill('Self-host test org');
      // page.getByRole('button', { name: /create/i }).click();
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Steps 6-8: add built-in test recipe, trigger ingestion, signal appears",
    async ({ page }) => {
      // TODO D1 + G1:
      // 1. Navigate to Settings → Recipes.
      // 2. Add the built-in test recipe.
      // 3. Trigger ingestion (or wait for auto-trigger).
      // 4. Signal appears in the feed within the test SLO.
      expect(true).toBe(true);
    }
  );
});
