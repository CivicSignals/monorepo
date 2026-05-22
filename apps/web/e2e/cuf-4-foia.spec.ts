// QA-1 — CUF-4: FOIA request lifecycle
// Spec: 11-qa-test-plan.md §2 CUF-4
//
// Status: SCAFFOLD — all tests are test.fixme() pending F1 (FOIA module).
//
// TODO F1: implement FOIA request CRUD + lifecycle → enable all steps.
// TODO C1: entity directory prerequisite for "Start FOIA request from entity profile".
// TODO B1: auth prerequisite.

import { test, expect } from "@playwright/test";

test.describe("CUF-4: FOIA request lifecycle", () => {
  test.fixme(
    "Step 1-3: start FOIA request from entity profile",
    async ({ page }) => {
      // TODO F1 + C1:
      // 1. Navigate to /entities/<id>.
      // 2. Click 'Start FOIA request'.
      // 3. Pick a WA template.
      // 4. Fill subject + topic fields.
      //    page.getByLabel('Subject').fill('Technology contracts 2024-2025');
      //    page.getByLabel('Topic').fill('Software licensing expenditures');
      // 5. Mark as sent (manual send method).
      //    page.getByRole('button', { name: /mark as sent/i }).click();
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Step 4: FOIA request transitions to acknowledged state",
    async ({ page }) => {
      // TODO F1:
      // page.getByRole('button', { name: /mark acknowledged/i }).click();
      // await expect(page.getByText(/acknowledged/i)).toBeVisible();
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Steps 5-7: PDF response upload triggers extraction within SLO",
    async ({ page }) => {
      // TODO F1 + E1 (extraction):
      // 1. Upload a test PDF response.
      //    page.getByLabel('Upload response').setInputFiles('fixtures/test-foia-response.pdf');
      // 2. Assert extraction job is queued.
      // 3. Poll until the resulting signal appears on the FOIA page (SLO: 5 min in test).
      expect(true).toBe(true);
    }
  );
});
