// Full-stack e2e — feed routing (the headline scenario).
//
// Tag: @fullstack. These tests need a SEEDED, running stack (web + api + DB +
// workers), so they run only in the `full-stack` Playwright project (grep
// /@fullstack/) against an externally-started stack — never in the *-smoke or
// browser projects, which have no backend. See `playwright.config.ts` and
// `docs/testing/e2e-rules.md`.
//
// What this proves (the per-workspace routing invariant from
// `apps/api/tests/e2e_fixtures/scenarios.yaml`):
//   - Alice (TX / school_district / rfp_posted) sees the TX school RFP and NOT
//     Bob's CA news.
//   - Bob (CA / news_mention) sees the CA county news and NOT the TX RFP.
//   - Alice's RFP detail page explains the match in the "Why this signal?"
//     panel (state TX / entity kind school_district / signal type rfp_posted).
//   - The weak_match signal (VT / library_system) is absent from BOTH feeds —
//     it scores below Alice's strict threshold and matches neither ICP (the
//     threshold / pre-filter gate).
//
// Seeded creds + expected titles come from `helpers/users.ts`, which is the TS
// mirror of scenarios.yaml + the golden `expected-signal.json` fixtures; assert
// only on those exact values and on `data-testid`s.

import { test, expect } from "@playwright/test";
import { loginAs } from "./helpers/auth";
import { FeedPage } from "./helpers/feed";
import { ALICE, BOB, WEAK_MATCH_SIGNAL_TITLE } from "./helpers/users";

test.describe("@fullstack feed routing — per-workspace signal scoping", () => {
  test("Alice's feed shows the TX RFP and not Bob's CA news", async ({
    page,
  }) => {
    await loginAs(page, ALICE);

    const feed = new FeedPage(page);
    await feed.goto();

    // Alice sees her TX school RFP …
    await feed.expectSignalVisible(ALICE.expectedSignalTitle);
    // … and does NOT see Bob's CA county news (it scored into Bob's workspace,
    // not hers — the feed is workspace-scoped).
    await feed.expectSignalAbsent(BOB.expectedSignalTitle);
    // … and never the out-of-ICP weak_match signal.
    await feed.expectSignalAbsent(WEAK_MATCH_SIGNAL_TITLE);
  });

  test("Bob's feed shows the CA news and not the TX RFP", async ({ page }) => {
    await loginAs(page, BOB);

    const feed = new FeedPage(page);
    await feed.goto();

    await feed.expectSignalVisible(BOB.expectedSignalTitle);
    await feed.expectSignalAbsent(ALICE.expectedSignalTitle);
    await feed.expectSignalAbsent(WEAK_MATCH_SIGNAL_TITLE);
  });

  test("Alice's RFP detail explains the match in 'Why this signal?'", async ({
    page,
  }) => {
    await loginAs(page, ALICE);

    const feed = new FeedPage(page);
    await feed.goto();

    const detail = await feed.openSignal(ALICE.expectedSignalTitle);
    await expect(detail.title()).toHaveText(ALICE.expectedSignalTitle);

    // The scorer seeds structured bullets the UI sentence-cases (workspace_scoring
    // `_explanation_bullets`): "matched state TX", "matched entity kind
    // school_district", "signal type rfp_posted is of interest". Assert the three
    // ICP axes that drove Alice's match appear in the panel.
    const bullets = await detail.whyBullets();
    const joined = bullets.join("\n");
    expect(bullets.length).toBeGreaterThan(0);
    expect(joined).toMatch(/matched state TX/i);
    expect(joined).toMatch(/school_district/i);
    expect(joined).toMatch(/rfp_posted/i);
  });

  test("the weak_match signal is absent from both feeds (threshold gate)", async ({
    browser,
  }) => {
    // Use two isolated contexts so the two sessions don't share localStorage.
    const aliceCtx = await browser.newContext();
    const bobCtx = await browser.newContext();
    try {
      const alicePage = await aliceCtx.newPage();
      const bobPage = await bobCtx.newPage();

      await loginAs(alicePage, ALICE);
      await loginAs(bobPage, BOB);

      const aliceFeed = new FeedPage(alicePage);
      const bobFeed = new FeedPage(bobPage);
      await aliceFeed.goto();
      await bobFeed.goto();

      await aliceFeed.expectSignalAbsent(WEAK_MATCH_SIGNAL_TITLE);
      await bobFeed.expectSignalAbsent(WEAK_MATCH_SIGNAL_TITLE);
    } finally {
      await aliceCtx.close();
      await bobCtx.close();
    }
  });
});
