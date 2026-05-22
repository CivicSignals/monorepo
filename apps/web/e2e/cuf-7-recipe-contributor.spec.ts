// QA-1 — CUF-7: Recipe contributor flow
// Spec: 11-qa-test-plan.md §2 CUF-7
//
// Note: This CUF is primarily a CI/workflow test (fork → PR → CI runs recipe
// against fixtures → human approval). The Playwright harness covers the web UI
// side of recipe status visibility in the admin UI, once that lands.
//
// Status: SCAFFOLD — all tests are test.fixme() pending D1 (recipe runner) + admin UI.
//
// TODO D1: recipe runner + fixture CI integration → CI steps 4-5 are non-UI.
// TODO admin recipes UI: recipe status page → enable step 6.

import { test, expect } from "@playwright/test";

test.describe("CUF-7: Recipe contributor flow (web UI side)", () => {
  test.fixme(
    "Step 5: recipe deployed to staging is visible in admin recipe list",
    async ({ page }) => {
      // TODO D1 + admin recipes UI:
      // await page.goto('/admin/recipes');
      // await expect(page.getByText('my_new.yml')).toBeVisible();
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Step 6: admin can trigger a live run of the recipe from the UI",
    async ({ page }) => {
      // TODO D1 + admin recipes UI:
      // page.getByRole('button', { name: /run recipe/i }).click();
      // await expect(page.getByText(/running|queued/i)).toBeVisible();
      expect(true).toBe(true);
    }
  );
});
