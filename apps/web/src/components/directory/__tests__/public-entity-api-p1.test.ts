// P1 — public-entities-api: unit tests for new contact and signal fetch helpers.
//
// Separate file from public-entity-p1.test.tsx to avoid vi.doMock collisions.
// Uses fetch spy pattern matching public-directory.test.tsx.

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import type { PublicContactPage, PublicContactRead, PublicSignalPage, PublicSignalRead } from "@/lib/public-entities-api";

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---- Fixtures ----

const CONTACT_1: PublicContactRead = {
  id: "contact-001",
  entity_id: "ent-p1-test",
  name: "Dr. Susan Park",
  department: "Office of the Superintendent",
  title: "Superintendent",
  status: "active",
  source: "NCES Staff Directory",
  source_url: "https://nces.ed.gov/staffdir/east",
  confidence: 0.95,
  verified: true,
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
};

const SIGNAL_1: PublicSignalRead = {
  id: "signal-001",
  entity_id: "ent-p1-test",
  entity_name_raw: "Eastside Unified School District",
  signal_type: "rfp_posted",
  title: "RFP: School Transportation Fleet 2026",
  summary: "District seeking bids for 45 electric school buses.",
  occurred_at: "2026-04-15T10:00:00Z",
  observed_at: "2026-04-16T08:00:00Z",
  confidence: 0.93,
  status: "active",
  created_at: "2026-04-16T08:00:00Z",
};

// ---- fetchPublicEntityContacts ----

describe("fetchPublicEntityContacts", () => {
  it("fetches from /contacts?entity_id= and returns a contact page", async () => {
    const page: PublicContactPage = { items: [CONTACT_1], next_cursor: null };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicEntityContacts } = await import("@/lib/public-entities-api");
    const result = await fetchPublicEntityContacts("ent-p1-test", { limit: 5 });

    expect(result.items).toHaveLength(1);
    expect(result.items[0].name).toBe("Dr. Susan Park");
    expect(result.items[0].source_url).toBe("https://nces.ed.gov/staffdir/east");
    expect(result.next_cursor).toBeNull();

    // Verify URL shape
    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/contacts");
    expect(calledUrl).toContain("entity_id=ent-p1-test");
    expect(calledUrl).toContain("limit=5");
  });

  it("uses the default limit of 5 when none is provided", async () => {
    const page: PublicContactPage = { items: [], next_cursor: null };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicEntityContacts } = await import("@/lib/public-entities-api");
    await fetchPublicEntityContacts("ent-test");

    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain("limit=5");
  });

  it("throws a descriptive error on non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("", { status: 500 }),
    );

    const { fetchPublicEntityContacts } = await import("@/lib/public-entities-api");
    await expect(fetchPublicEntityContacts("ent-bad")).rejects.toThrow(
      /Failed to fetch contacts for entity ent-bad/,
    );
  });

  it("returns the next_cursor for pagination", async () => {
    const page: PublicContactPage = { items: [CONTACT_1], next_cursor: "cursor-abc" };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicEntityContacts } = await import("@/lib/public-entities-api");
    const result = await fetchPublicEntityContacts("ent-p1-test");
    expect(result.next_cursor).toBe("cursor-abc");
  });
});

// ---- fetchPublicEntitySignals ----

describe("fetchPublicEntitySignals", () => {
  it("fetches from /signals?entity_id= and returns a signal page", async () => {
    const page: PublicSignalPage = { items: [SIGNAL_1], next_cursor: null };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicEntitySignals } = await import("@/lib/public-entities-api");
    const result = await fetchPublicEntitySignals("ent-p1-test", { limit: 3 });

    expect(result.items).toHaveLength(1);
    expect(result.items[0].title).toBe("RFP: School Transportation Fleet 2026");
    expect(result.items[0].signal_type).toBe("rfp_posted");
    expect(result.next_cursor).toBeNull();

    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/signals");
    expect(calledUrl).toContain("entity_id=ent-p1-test");
    expect(calledUrl).toContain("limit=3");
  });

  it("uses the default limit of 3 when none is provided", async () => {
    const page: PublicSignalPage = { items: [], next_cursor: null };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicEntitySignals } = await import("@/lib/public-entities-api");
    await fetchPublicEntitySignals("ent-test");

    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain("limit=3");
  });

  it("throws a descriptive error on non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("", { status: 503 }),
    );

    const { fetchPublicEntitySignals } = await import("@/lib/public-entities-api");
    await expect(fetchPublicEntitySignals("ent-bad")).rejects.toThrow(
      /Failed to fetch signals for entity ent-bad/,
    );
  });

  it("returns the next_cursor for pagination", async () => {
    const page: PublicSignalPage = { items: [SIGNAL_1], next_cursor: "cursor-xyz" };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicEntitySignals } = await import("@/lib/public-entities-api");
    const result = await fetchPublicEntitySignals("ent-p1-test");
    expect(result.next_cursor).toBe("cursor-xyz");
  });
});
