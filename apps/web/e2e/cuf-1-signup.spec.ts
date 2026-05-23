// CUF-1: Signup → first signal seen — full-stack e2e.
// Spec: 11-qa-test-plan.md §2, user journey: 04-user-journeys.md J1 + J2
//
// Tag: @fullstack. Needs a running stack (web + api + DB). Runs only in the
// `full-stack` Playwright project against an externally-started stack — never in
// *-smoke / browser projects. See playwright.config.ts + docs/testing/e2e-rules.md.
//
// Now that auth (B1), workspace create (B5), the ICP wizard (F2), and the feed
// (G1) all exist, the headline step — a brand-new user signs up and reaches a
// (initially empty) feed — is driven for real below. Two sub-steps remain
// test.fixme() because their UI seam genuinely isn't drivable headlessly yet:
//   - email verification requires polling Mailpit for the link (no UI affordance);
//   - the ICP wizard's final step redirects to `/`, not `/feed` (TODO G1 in
//     icp-wizard.tsx), so "land on the feed straight from onboarding" can't be
//     asserted without that redirect. Each is annotated inline.
//
// Per-run uniqueness: the signup email embeds Date.now() so reruns never collide
// with a previously-created account (determinism rule, docs/testing/e2e-rules.md).

import { test, expect } from "@playwright/test";

/** A unique signup identity per run (no cross-run collisions on the real DB). */
function freshSignup() {
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  return {
    email: `cuf1+${stamp}@civicsignals.test`,
    password: "Sup3rSecret-cuf1!",
    name: "CUF-1 New User",
    workspaceName: `CUF-1 Org ${stamp}`,
  };
}

test.describe("@fullstack CUF-1: Signup → first signal seen", () => {
  test("Step 1: a new user can sign up with email + password", async ({
    page,
  }) => {
    const user = freshSignup();

    await page.goto("/signup");
    await expect(page.getByTestId("signup-form")).toBeVisible();

    await page.getByTestId("signup-name").fill(user.name);
    await page.getByTestId("signup-email").fill(user.email);
    await page.getByTestId("signup-password").fill(user.password);
    await page.getByTestId("signup-submit").click();

    // Signup succeeds → the form swaps to the "Check your inbox" success panel
    // that echoes the new account's email (the API issued a token + told us
    // whether verification is required).
    const success = page.getByTestId("signup-success");
    await expect(success).toBeVisible();
    await expect(success).toContainText(user.email);
  });

  test("Step 2-3: new user creates a workspace and lands in the app", async ({
    page,
  }) => {
    const user = freshSignup();

    // Sign up (real) — leaves us with a client session (token in cs.session).
    await page.goto("/signup");
    await page.getByTestId("signup-email").fill(user.email);
    await page.getByTestId("signup-password").fill(user.password);
    await page.getByTestId("signup-submit").click();
    await expect(page.getByTestId("signup-success")).toBeVisible();

    // The authed header now renders the workspace switcher with no workspaces
    // yet. Create one via its real inline-create flow (B5).
    await page.goto("/feed");
    const switcher = page.getByTestId("workspace-switcher");
    await expect(switcher).toBeVisible();
    await switcher.getByRole("button", { name: /New workspace/i }).click();
    await switcher.getByPlaceholder("Workspace name").fill(user.workspaceName);
    await switcher.getByRole("button", { name: /^Create$/i }).click();

    // The newly-created workspace becomes active (useCreateWorkspace sets the
    // active id) and appears as the selected option in the switcher.
    const select = switcher.getByLabel("Active workspace");
    await expect(select).toBeVisible();
    await expect(switcher).toContainText(user.workspaceName);
  });

  test("Step 4: with a workspace but no ICP, the feed shows the empty state", async ({
    page,
  }) => {
    const user = freshSignup();

    await page.goto("/signup");
    await page.getByTestId("signup-email").fill(user.email);
    await page.getByTestId("signup-password").fill(user.password);
    await page.getByTestId("signup-submit").click();
    await expect(page.getByTestId("signup-success")).toBeVisible();

    await page.goto("/feed");
    const switcher = page.getByTestId("workspace-switcher");
    await switcher.getByRole("button", { name: /New workspace/i }).click();
    await switcher.getByPlaceholder("Workspace name").fill(user.workspaceName);
    await switcher.getByRole("button", { name: /^Create$/i }).click();
    await expect(switcher).toContainText(user.workspaceName);

    // A brand-new workspace has no scored signals → the feed's "no signals yet"
    // empty state, NOT the auth-required prompt (we are signed in with a
    // workspace active) and NOT the filtered-empty state (no filters set).
    await page.goto("/feed");
    await expect(page.getByTestId("feed-empty")).toBeVisible();
    await expect(page.getByTestId("feed-auth-required")).toHaveCount(0);
  });

  test.fixme(
    "Step 2 (verification): verification email arrives and link works (Mailpit)",
    async ({ page }) => {
      // FIXME: no UI affordance for this — it needs the test to poll Mailpit's
      // HTTP API for the verification message and extract the link, then visit
      // it. In test envs the API mails via Mailpit (http://localhost:8025).
      //
      //   const link = await waitForMailpitLink(user.email);  // helper TBD
      //   await page.goto(link);
      //   await expect(page).toHaveURL(/verified|login|onboarding/i);
      //
      // Keep fixme until a Mailpit helper lands; the rest of CUF-1 above does
      // not depend on verification (the API issues a usable session at signup).
      expect(page).toBeTruthy();
    },
  );

  test.fixme(
    "Step 5: completing the ICP wizard lands the user on the feed",
    async ({ page }) => {
      // FIXME: the ICP wizard (F2, components/icp/icp-wizard.tsx) currently
      // redirects to `/` on submit, not `/feed` (see its `TODO G1: redirect to
      // /signals (feed) once that route exists`). Until that redirect targets
      // the feed, "finish onboarding → land on the feed" can't be asserted as a
      // single flow. The wizard steps are also multi-select widgets without
      // data-testids, so driving them reliably needs testids added first
      // (kept out of scope for this layer to avoid guessing the final UI).
      //
      // When unblocked: drive geography (name + TX) → segments (school_district)
      // → size (skip) → signals (rfp_posted) → keywords (threshold) → review
      // (Create & activate ICP), then expect /feed and feed-empty (no seeded
      // signals for a brand-new ICP) or a populated list if the seed ran.
      expect(page).toBeTruthy();
    },
  );

  test.fixme(
    "Step 6-7: signal detail shows source docs + extracted fields (seeded)",
    async ({ page }) => {
      // FIXME: the seeded-signal detail path is fully exercised by
      // feed-routing.spec.ts (Alice's RFP detail). This CUF-1 variant — a
      // brand-new self-onboarded user reaching a detail page — depends on Step 5
      // (ICP via the wizard) plus a seed of signals for that fresh ICP, neither
      // of which is wired yet. See feed-routing.spec.ts for the detail-page
      // coverage that does run today.
      expect(page).toBeTruthy();
    },
  );
});
