// Unit tests for the ICP wizard Zod schemas (F2).
// No DOM needed — node environment.

import { describe, it, expect } from "vitest";
import {
  geographyStepSchema,
  segmentsStepSchema,
  sizeStepSchema,
  signalsStepSchema,
  keywordsStepSchema,
  icpWizardSchema,
} from "../icp-schemas";

// ---- geographyStepSchema ----

describe("geographyStepSchema", () => {
  it("accepts a valid geography payload", () => {
    const result = geographyStepSchema.safeParse({
      name: "My ICP",
      countries: ["US"],
      states: ["TX", "WA"],
    });
    expect(result.success).toBe(true);
  });

  it("upcases country codes", () => {
    const result = geographyStepSchema.safeParse({
      name: "My ICP",
      countries: ["us"],
      states: [],
    });
    expect(result.success).toBe(true);
    if (result.success) expect(result.data.countries).toEqual(["US"]);
  });

  it("rejects a country code longer than 2 chars", () => {
    const result = geographyStepSchema.safeParse({
      name: "My ICP",
      countries: ["USA"],
      states: [],
    });
    expect(result.success).toBe(false);
  });

  it("rejects empty countries array", () => {
    const result = geographyStepSchema.safeParse({
      name: "My ICP",
      countries: [],
      states: [],
    });
    expect(result.success).toBe(false);
  });

  it("requires a non-empty name", () => {
    const result = geographyStepSchema.safeParse({
      name: "",
      countries: ["US"],
      states: [],
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path[0] === "name")).toBe(true);
    }
  });

  it("accepts empty states (= all states)", () => {
    const result = geographyStepSchema.safeParse({
      name: "My ICP",
      countries: ["US"],
      states: [],
    });
    expect(result.success).toBe(true);
  });
});

// ---- segmentsStepSchema ----

describe("segmentsStepSchema", () => {
  it("accepts an empty entity_kinds list (= all)", () => {
    expect(segmentsStepSchema.safeParse({ entity_kinds: [] }).success).toBe(true);
  });

  it("accepts valid entity kinds", () => {
    const result = segmentsStepSchema.safeParse({
      entity_kinds: ["k12_district", "community_college"],
    });
    expect(result.success).toBe(true);
  });

  it("rejects unknown entity kinds", () => {
    const result = segmentsStepSchema.safeParse({
      entity_kinds: ["unknown_kind"],
    });
    expect(result.success).toBe(false);
  });
});

// ---- sizeStepSchema ----

describe("sizeStepSchema", () => {
  it("accepts all null (no filter)", () => {
    expect(
      sizeStepSchema.safeParse({
        min_size: null,
        max_size: null,
        deal_band_min_cents: null,
        deal_band_max_cents: null,
      }).success,
    ).toBe(true);
  });

  it("accepts a valid size range", () => {
    expect(
      sizeStepSchema.safeParse({
        min_size: 1000,
        max_size: 50000,
        deal_band_min_cents: null,
        deal_band_max_cents: null,
      }).success,
    ).toBe(true);
  });

  it("rejects inverted size range (min > max)", () => {
    const result = sizeStepSchema.safeParse({
      min_size: 50000,
      max_size: 1000,
      deal_band_min_cents: null,
      deal_band_max_cents: null,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path[0] === "max_size")).toBe(true);
    }
  });

  it("rejects inverted deal band (min > max)", () => {
    const result = sizeStepSchema.safeParse({
      min_size: null,
      max_size: null,
      deal_band_min_cents: 50_000_00,
      deal_band_max_cents: 10_000_00,
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(
        result.error.issues.some((i) => i.path[0] === "deal_band_max_cents"),
      ).toBe(true);
    }
  });
});

// ---- signalsStepSchema ----

describe("signalsStepSchema", () => {
  it("accepts a valid signal types + weights payload", () => {
    const result = signalsStepSchema.safeParse({
      signal_types: ["rfp_posted", "budget_drafted"],
      signal_weights: { rfp_posted: 1.0, budget_drafted: 0.7 },
    });
    expect(result.success).toBe(true);
  });

  it("requires at least one signal type", () => {
    const result = signalsStepSchema.safeParse({
      signal_types: [],
      signal_weights: {},
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(
        result.error.issues.some((i) => i.path[0] === "signal_types"),
      ).toBe(true);
    }
  });

  it("rejects an unknown signal type", () => {
    const result = signalsStepSchema.safeParse({
      signal_types: ["unknown_type"],
      signal_weights: {},
    });
    expect(result.success).toBe(false);
  });

  it("rejects a weight > 1", () => {
    const result = signalsStepSchema.safeParse({
      signal_types: ["rfp_posted"],
      signal_weights: { rfp_posted: 1.5 },
    });
    expect(result.success).toBe(false);
  });
});

// ---- keywordsStepSchema ----

describe("keywordsStepSchema", () => {
  it("accepts empty keyword lists and default threshold", () => {
    expect(
      keywordsStepSchema.safeParse({
        keywords_required: [],
        keywords_excluded: [],
        threshold: 50,
      }).success,
    ).toBe(true);
  });

  it("rejects threshold > 100", () => {
    const result = keywordsStepSchema.safeParse({
      keywords_required: [],
      keywords_excluded: [],
      threshold: 101,
    });
    expect(result.success).toBe(false);
  });

  it("rejects threshold < 0", () => {
    const result = keywordsStepSchema.safeParse({
      keywords_required: [],
      keywords_excluded: [],
      threshold: -1,
    });
    expect(result.success).toBe(false);
  });
});

// ---- icpWizardSchema (full composed schema) ----

describe("icpWizardSchema", () => {
  it("assembles a complete valid ICP payload", () => {
    const result = icpWizardSchema.safeParse({
      name: "K-12 Texas",
      countries: ["US"],
      states: ["TX"],
      entity_kinds: ["k12_district"],
      min_size: 5000,
      max_size: null,
      deal_band_min_cents: null,
      deal_band_max_cents: null,
      signal_types: ["rfp_posted", "budget_drafted"],
      signal_weights: { rfp_posted: 1.0, budget_drafted: 0.8 },
      keywords_required: ["analytics"],
      keywords_excluded: ["charter"],
      threshold: 60,
    });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.countries).toEqual(["US"]);
      expect(result.data.entity_kinds).toEqual(["k12_district"]);
      expect(result.data.signal_types).toContain("rfp_posted");
      expect(result.data.threshold).toBe(60);
    }
  });
});
