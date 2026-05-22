// Unit tests for the saved-search form Zod schema (H1).
// No DOM needed — node environment.

import { describe, expect, it } from "vitest";
import {
  FILTER_MESSAGES,
  savedSearchFormSchema,
} from "../searches-schemas";

/** Collect the issue message(s) for a given field path. */
function messagesFor(
  result: ReturnType<typeof savedSearchFormSchema.safeParse>,
  field: string,
): string[] {
  if (result.success) return [];
  return result.error.issues
    .filter((i) => i.path[0] === field)
    .map((i) => i.message);
}

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

  it("rejects an out-of-range min_score with an explicit message", () => {
    const high = savedSearchFormSchema.safeParse({ name: "x", min_score: 150 });
    expect(high.success).toBe(false);
    expect(messagesFor(high, "min_score")).toContain(FILTER_MESSAGES.scoreRange);
    expect(
      savedSearchFormSchema.safeParse({ name: "x", min_score: -1 }).success,
    ).toBe(false);
  });

  it("rejects an inverted date range with an explicit message", () => {
    const result = savedSearchFormSchema.safeParse({
      name: "x",
      published_at_gte: "2026-02-01T00:00",
      published_at_lt: "2026-01-01T00:00",
    });
    expect(result.success).toBe(false);
    expect(messagesFor(result, "published_at_gte")).toContain(
      FILTER_MESSAGES.dateRangeInverted,
    );
  });

  it("rejects an equal date range (empty window)", () => {
    const moment = "2026-01-01T00:00";
    const result = savedSearchFormSchema.safeParse({
      name: "x",
      published_at_gte: moment,
      published_at_lt: moment,
    });
    expect(result.success).toBe(false);
    expect(messagesFor(result, "published_at_gte")).toContain(
      FILTER_MESSAGES.dateRangeInverted,
    );
  });

  it("accepts a valid (start < end) date range", () => {
    const result = savedSearchFormSchema.safeParse({
      name: "x",
      published_at_gte: "2026-01-01T00:00",
      published_at_lt: "2026-02-01T00:00",
    });
    expect(result.success).toBe(true);
  });

  it("rejects an unparseable date with an explicit message", () => {
    const result = savedSearchFormSchema.safeParse({
      name: "x",
      published_at_gte: "not-a-date",
    });
    expect(result.success).toBe(false);
    expect(messagesFor(result, "published_at_gte")).toContain(
      FILTER_MESSAGES.invalidDate,
    );
  });
});
