// N6 — Unit tests for the pricing page data and structure.
// Tests the plan tier names, comparison heading, and key product invariants
// without requiring DOM/React rendering (no jsdom needed).

import { describe, it, expect } from "vitest";
import {
  PLANS,
  PLAN_NAMES,
  COMPARISON_HEADING,
} from "../pricing-data";

describe("PricingData — plan tiers", () => {
  it("includes all five expected tier names", () => {
    expect(PLAN_NAMES).toContain("Self-Host");
    expect(PLAN_NAMES).toContain("Solo");
    expect(PLAN_NAMES).toContain("Starter");
    expect(PLAN_NAMES).toContain("Pro");
    expect(PLAN_NAMES).toContain("Enterprise");
  });

  it("has exactly five plans", () => {
    expect(PLANS).toHaveLength(5);
  });

  it("SMB plans (Self-Host, Solo, Starter, Pro) are self-serve", () => {
    const smb = PLANS.filter((p) => p.id !== "enterprise");
    smb.forEach((plan) => {
      expect(plan.selfServe, `${plan.name} should be self-serve`).toBe(true);
    });
  });

  it("Enterprise plan is not self-serve (contact required)", () => {
    const enterprise = PLANS.find((p) => p.id === "enterprise");
    expect(enterprise).toBeDefined();
    expect(enterprise!.selfServe).toBe(false);
  });

  it("every SMB CTA links to /signup (not a contact-sales wall)", () => {
    const smb = PLANS.filter(
      (p) => p.id !== "enterprise" && p.id !== "self-host",
    );
    smb.forEach((plan) => {
      expect(
        plan.ctaHref,
        `${plan.name} CTA should link to /signup`,
      ).toMatch(/^\/signup/);
    });
  });

  it("Starter plan is highlighted as 'most popular'", () => {
    const starter = PLANS.find((p) => p.id === "starter");
    expect(starter).toBeDefined();
    expect(starter!.highlighted).toBe(true);
  });

  it("each plan has at least one feature", () => {
    PLANS.forEach((plan) => {
      expect(
        plan.features.length,
        `${plan.name} should have features`,
      ).toBeGreaterThan(0);
    });
  });

  it("self-host plan has $0 price", () => {
    const selfHost = PLANS.find((p) => p.id === "self-host");
    expect(selfHost).toBeDefined();
    expect(selfHost!.monthlyPrice).toBe("$0");
  });

  it("enterprise plan has no fixed monthly price (custom)", () => {
    const enterprise = PLANS.find((p) => p.id === "enterprise");
    expect(enterprise).toBeDefined();
    expect(enterprise!.monthlyPrice).toBeNull();
  });
});

describe("PricingComparison — heading", () => {
  it("exports the expected comparison section heading", () => {
    expect(COMPARISON_HEADING).toBe(
      "How CivicSignals compares to incumbent SLED tools",
    );
  });
});
