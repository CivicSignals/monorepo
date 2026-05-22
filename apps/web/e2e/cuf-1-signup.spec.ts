// QA-1 — CUF-1: Signup → first signal seen
// Spec: 11-qa-test-plan.md §2, user journey: 04-user-journeys.md J1 + J2
//
// Status: SCAFFOLD — all tests are test.fixme() pending B1 (auth) + G1 (signal feed).
//
// TODO B1: implement signup / email-verification / login flows → enable steps 1-6.
// TODO G1: implement signal feed → enable step 7 (sees signals after signup).
// TODO B5: implement workspace creation step → enable step 4.
// TODO B2: implement ICP onboarding wizard → enable step 5.
//
// Pass bar (per doc 11 §2 CUF-1):
//   All steps complete in under 30 seconds of clock time.
//   Final dashboard interactive within 2 seconds.

import { test, expect } from "@playwright/test";

test.describe("CUF-1: Signup → first signal seen", () => {
  test.fixme(
    "Step 1-3: new user can sign up with email and password",
    async ({ page }) => {
      // TODO B1: implement auth module.
      //
      // 1. Navigate to /signup.
      // 2. Fill in email + password form.
      //    page.getByLabel('Email').fill('testuser+cuf1@example.com');
      //    page.getByLabel('Password').fill('SecurePass1!');
      //    page.getByRole('button', { name: /Create account/i }).click();
      // 3. Expect to land on a "check your email" confirmation page.
      //    await expect(page).toHaveURL(/verify|confirm/i);
      expect(true).toBe(true); // placeholder
    }
  );

  test.fixme(
    "Step 2: verification email arrives and link works (Mailpit)",
    async ({ page }) => {
      // TODO B1: wire Mailpit API to extract verification link.
      //
      // In test envs NEXT_PUBLIC_EMAIL_VERIFICATION=mailpit; the test
      // polls http://localhost:8025/api/v1/messages and extracts the link.
      //
      // const mailpit = new MailpitClient('http://localhost:8025');
      // const link = await mailpit.waitForLink('testuser+cuf1@example.com');
      // await page.goto(link);
      // await expect(page).toHaveURL(/dashboard|onboarding/i);
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Step 4: workspace creation after verification",
    async ({ page }) => {
      // TODO B5: implement workspace creation.
      //
      // After email verification the user is prompted for workspace details:
      //   page.getByLabel('Company name').fill('Test Org');
      //   page.getByRole('button', { name: /Create workspace/i }).click();
      //   await expect(page).toHaveURL(/onboarding/i);
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Step 5: 5-step ICP onboarding wizard completes",
    async ({ page }) => {
      // TODO B2: implement ICP onboarding (steps 1–5 per 04-user-journeys.md J2).
      //
      // Step 1 (ICP):
      //   page.getByPlaceholder(/what do you sell/i)
      //       .fill('K-12 learning analytics SaaS for districts > 5,000 students');
      //   page.getByRole('button', { name: /Continue/i }).click();
      // Step 2 (signal types):
      //   page.getByText('RFP posted').click();
      //   page.getByRole('button', { name: /Continue/i }).click();
      // Step 3 (connect destination): skip
      //   page.getByRole('button', { name: /Skip for now/i }).click();
      // Step 4 (first saved search):
      //   page.getByRole('button', { name: /See my first signals/i }).click();
      // Step 5: assert dashboard / feed.
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Step 6-7: after onboarding, dashboard shows at least one signal",
    async ({ page }) => {
      // TODO G1: implement signal feed (the test seed populates pre-scored
      // signals for the test ICP so at least one is visible immediately).
      //
      // await expect(page).toHaveURL(/dashboard|feed/i);
      // const signals = page.getByTestId('signal-card');
      // await expect(signals.first()).toBeVisible();
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "Step 8-9: signal detail page shows source docs and extracted fields",
    async ({ page }) => {
      // TODO G1 + E4: signal detail page (wireframes 05-wireframes.md §5).
      //
      // const firstSignal = page.getByTestId('signal-card').first();
      // await firstSignal.click();
      // await expect(page).toHaveURL(/\/signals\//i);
      // await expect(page.getByText(/source/i).first()).toBeVisible();
      // await expect(page.getByText(/suggested contacts/i)).toBeVisible();
      expect(true).toBe(true);
    }
  );

  test.fixme(
    "CUF-1 end-to-end: full flow completes in under 30 seconds",
    async ({ page }) => {
      // TODO B1 + B2 + B5 + G1: combine all steps above and assert total
      // wall-clock time is < 30 seconds (per doc 11 §2 CUF-1 pass bar).
      expect(true).toBe(true);
    }
  );
});
