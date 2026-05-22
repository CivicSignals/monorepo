// Unit tests for the saved-search form Zod schema (H1).
// No DOM needed — node environment.

import { describe, expect, it } from "vitest";
import { savedSearchFormSchema } from "../searches-schemas";

describe("savedSearchFormSchema", () => {
  it("accepts a fully specified search", () => {
    const result = savedSearchFormSchema.safeParse({
      name: "Hot RFPs",
      signal_type: "rfp_posted",
      statuses: ["new", "pinned"],
      min_score: 60,
      is_shared: true,
    });
    expect(result.success).toBe(true);
  });

  it("accepts an empty (any-type, no-filter) search with just a name", () => {
    const result = savedSearchFormSchema.safeParse({ name: "Everything" });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.signal_type).toBe("");
      expect(result.data.statuses).toEqual([]);
      expect(result.data.min_score).toBeNull();
      expect(result.data.is_shared).toBe(false);
    }
  });

  it("rejects an empty name", () => {
    const result = savedSearchFormSchema.safeParse({ name: "  " });
    expect(result.success).toBe(false);
  });

  it("rejects an unknown signal type", () => {
    const result = savedSearchFormSchema.safeParse({
      name: "x",
      signal_type: "not_a_type",
    });
    expect(result.success).toBe(false);
  });

  it("rejects an unknown status", () => {
    const result = savedSearchFormSchema.safeParse({
      name: "x",
      statuses: ["bogus"],
    });
    expect(result.success).toBe(false);
  });

  it("rejects an out-of-range min_score", () => {
    expect(
      savedSearchFormSchema.safeParse({ name: "x", min_score: 150 }).success,
    ).toBe(false);
    expect(
      savedSearchFormSchema.safeParse({ name: "x", min_score: -1 }).success,
    ).toBe(false);
  });
});
