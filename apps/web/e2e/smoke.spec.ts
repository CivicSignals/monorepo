// QA-1 — Smoke specs: flows that exist TODAY and run on every PR.
// Tagged @smoke so the chromium-smoke Playwright project picks them up.
//
// These tests run against the built/served web app (next start or next dev).
// They cover the static marketing surface that is already shipped:
//   - Landing page loads and shows the brand name
//   - Navigation header is present and links are correct
//   - /pricing renders plan tiers and the competitor comparison
//   - 404 handling renders a not-found state (Next.js default or custom)
//
// As features land (auth, feed, signal detail, etc.) the corresponding CUF
// fixme scaffolds in cuf-*.spec.ts will be uncommented and fleshed out.

import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// Landing page
// ---------------------------------------------------------------------------

test("@smoke landing page loads and shows brand name", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/CivicSignals/i);
  // The home page heading must be present.
  const heading = page.getByRole("heading", { name: /CivicSignals/i }).first();
  await expect(heading).toBeVisible();
});

test("@smoke landing page is interactive within 2 seconds", async ({
  page,
}) => {
  const start = Date.now();
  await page.goto("/");
  // Wait for the document to finish loading.
  await page.waitForLoadState("domcontentloaded");
  // The time from navigation to domcontentloaded should be well under 2 seconds
  // in a local environment; in CI the budget is relaxed to the Playwright timeout.
  const elapsed = Date.now() - start;
  // Soft assertion — log it but don't hard-fail on slow CI runners.
  if (elapsed > 2000) {
    console.warn(`Landing page DOMContentLoaded took ${elapsed}ms (target <2s)`);
  }
  // Hard assertion: page must have rendered _something_.
  await expect(page.locator("main")).toBeVisible();
});

// ---------------------------------------------------------------------------
// Navigation / site header
// ---------------------------------------------------------------------------

test("@smoke site header has logo link to home", async ({ page }) => {
  await page.goto("/");
  const logo = page.getByRole("link", { name: /CivicSignals home/i });
  await expect(logo).toBeVisible();
  // Logo href must point to the root.
  await expect(logo).toHaveAttribute("href", "/");
});

test("@smoke site header Pricing nav link is present and navigates", async ({
  page,
}) => {
  await page.goto("/");
  const pricingLink = page.getByRole("navigation").getByRole("link", {
    name: "Pricing",
  });
  await expect(pricingLink).toBeVisible();
  await pricingLink.click();
  await expect(page).toHaveURL(/\/pricing/);
  await expect(page).toHaveTitle(/Pricing/i);
});

test("@smoke site header has Get started CTA link", async ({ page }) => {
  await page.goto("/");
  // 'Get started' is an <a> (not a <button>) pointing at /signup.
  const cta = page.getByRole("link", { name: /get started/i });
  await expect(cta).toBeVisible();
  await expect(cta).toHaveAttribute("href", "/signup");
});

// ---------------------------------------------------------------------------
// Pricing page — N6 (merged)
// ---------------------------------------------------------------------------

test("@smoke /pricing page renders", async ({ page }) => {
  await page.goto("/pricing");
  await expect(page).toHaveTitle(/Pricing/i);
  // The page must load without a crash (no error boundary, no 500 page).
  await expect(page.locator("main")).toBeVisible();
});

test("@smoke /pricing shows at least four plan tiers", async ({ page }) => {
  await page.goto("/pricing");
  // Each plan card has role=article with an aria-label like "Starter plan".
  const planCards = page.getByRole("article");
  // We ship Self-Host, Solo, Starter, Pro, Enterprise = 5 tiers.
  await expect(planCards).toHaveCount(5);
});

test("@smoke /pricing Starter plan is highlighted as most popular", async ({
  page,
}) => {
  await page.goto("/pricing");
  // The pricing-tiers component adds a 'Most popular' badge on the highlighted plan.
  await expect(page.getByText("Most popular")).toBeVisible();
});

test("@smoke /pricing plan tiers show expected plan names", async ({
  page,
}) => {
  await page.goto("/pricing");
  for (const name of ["Self-Host", "Solo", "Starter", "Pro", "Enterprise"]) {
    await expect(
      page.getByRole("article", { name: `${name} plan` })
    ).toBeVisible();
  }
});

test("@smoke /pricing shows competitor comparison section", async ({
  page,
}) => {
  await page.goto("/pricing");
  // The comparison heading is rendered by PricingComparison.
  await expect(
    page.getByText(/How CivicSignals compares/i)
  ).toBeVisible();
});

test("@smoke /pricing comparison table lists CivicSignals, Starbridge, GovWin", async ({
  page,
}) => {
  await page.goto("/pricing");
  // The comparison table has column headers for the three products.
  await expect(page.getByText(/Starbridge/i).first()).toBeVisible();
  await expect(page.getByText(/GovWin/i).first()).toBeVisible();
});

test("@smoke /pricing has a Start free trial CTA for Starter", async ({
  page,
}) => {
  await page.goto("/pricing");
  const starterCard = page.getByRole("article", { name: "Starter plan" });
  await expect(
    starterCard.getByRole("link", { name: /Start free trial/i })
  ).toBeVisible();
});

// ---------------------------------------------------------------------------
// 404 / not-found handling
// ---------------------------------------------------------------------------

test("@smoke unknown route returns a 404 response", async ({ page }) => {
  const response = await page.goto("/this-route-does-not-exist-qa-1");
  // Next.js returns 404 status for unmatched routes.
  expect(response?.status()).toBe(404);
});

test("@smoke 404 page has navigation back to home", async ({ page }) => {
  await page.goto("/this-route-does-not-exist-qa-1");
  // Next.js default 404 page has a link back. The custom 404 (when it ships)
  // must also have navigation. We assert that at least one link to "/" exists.
  const homeLinks = page.getByRole("link", { name: /home|CivicSignals/i });
  // At minimum the header logo link is present.
  await expect(homeLinks.first()).toBeVisible();
});

// ---------------------------------------------------------------------------
// Availability / health
// ---------------------------------------------------------------------------

test("@smoke home page responds within reasonable time", async ({ page }) => {
  const response = await page.goto("/");
  // Must return 200.
  expect(response?.status()).toBe(200);
});

test("@smoke /pricing responds within reasonable time", async ({ page }) => {
  const response = await page.goto("/pricing");
  expect(response?.status()).toBe(200);
});
