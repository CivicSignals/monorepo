// P2 — public-signals-api: unit tests for the public signal fetch helpers.
//
// Mirrors the C5 public-entity-api-p1.test.ts fetch-spy pattern. These helpers are
// the server-side transport for the public /s/[id] signal pages; they call the
// unauthenticated /signals read API (no auth, no X-Workspace-Id).

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import type {
  PublicSignalRead,
  PublicSignalSources,
  PublicSignalPage,
} from "@/lib/public-signals-api";
import type { SignalRead } from "@/lib/signals-api";

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---- Fixtures ----

// The narrowed projection GET /signals/:id/public returns (P2).
const PUBLIC_SIGNAL_1: PublicSignalRead = {
  id: "signal-001",
  entity_name: "Eastside Unified School District",
  signal_type: "rfp_posted",
  occurred_at: "2026-04-15T10:00:00Z",
  observed_at: "2026-04-16T08:00:00Z",
  summary: "District seeking bids for 45 electric school buses.",
  title: "RFP: School Transportation Fleet 2026",
};

// The full SignalRead shape the GET /signals list returns (used by the sitemap walk).
const SIGNAL_1: SignalRead = {
  id: "signal-001",
  entity_id: "ent-001",
  entity_name_raw: "Eastside Unified School District",
  signal_type: "rfp_posted",
  recipe_id: "ca_k12_rfps",
  raw_document_ids: ["doc-001", "doc-002"],
  content_hash: "abc123",
  occurred_at: "2026-04-15T10:00:00Z",
  observed_at: "2026-04-16T08:00:00Z",
  summary: "District seeking bids for 45 electric school buses.",
  title: "RFP: School Transportation Fleet 2026",
  details: { due_at: "2026-06-01T17:00:00Z" },
  confidence: 0.93,
  status: "new",
  is_degraded: false,
  review_required: false,
  created_at: "2026-04-16T08:00:00Z",
};

const SOURCES_1: PublicSignalSources = {
  signal_id: "signal-001",
  sources: [
    {
      document_id: "doc-001",
      source_url: "https://eastsideusd.org/rfps/2026-transport",
      recipe_id: "ca_k12_rfps",
      fetched_at: "2026-04-16T07:30:00Z",
    },
  ],
};

// ---- fetchPublicSignal ----

describe("fetchPublicSignal", () => {
  it("fetches the narrowed /signals/:id/public projection and returns it", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(PUBLIC_SIGNAL_1), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicSignal } = await import("@/lib/public-signals-api");
    const result = await fetchPublicSignal("signal-001");

    expect(result?.title).toBe("RFP: School Transportation Fleet 2026");
    expect(result?.signal_type).toBe("rfp_posted");
    expect(result?.entity_name).toBe("Eastside Unified School District");

    // Hits the public projection endpoint, not the full internal /signals/:id read.
    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/signals/signal-001/public");
  });

  it("returns null on 404 (missing or non-public-type signal)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("", { status: 404 }),
    );

    const { fetchPublicSignal } = await import("@/lib/public-signals-api");
    const result = await fetchPublicSignal("missing");
    expect(result).toBeNull();
  });

  it("throws a descriptive error on a non-404 error response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("", { status: 500 }),
    );

    const { fetchPublicSignal } = await import("@/lib/public-signals-api");
    await expect(fetchPublicSignal("boom")).rejects.toThrow(
      /Failed to fetch signal boom/,
    );
  });

  it("sends no Authorization or X-Workspace-Id header (public read)", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(PUBLIC_SIGNAL_1), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicSignal } = await import("@/lib/public-signals-api");
    await fetchPublicSignal("signal-001");

    const init = fetchSpy.mock.calls[0][1] as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.has("authorization")).toBe(false);
    expect(headers.has("x-workspace-id")).toBe(false);
  });
});

// ---- fetchPublicSignalSources ----

describe("fetchPublicSignalSources", () => {
  it("fetches /signals/:id/sources and returns the citations", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(SOURCES_1), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicSignalSources } = await import("@/lib/public-signals-api");
    const result = await fetchPublicSignalSources("signal-001");

    expect(result.signal_id).toBe("signal-001");
    expect(result.sources).toHaveLength(1);
    expect(result.sources[0].source_url).toBe(
      "https://eastsideusd.org/rfps/2026-transport",
    );
    expect(result.sources[0].recipe_id).toBe("ca_k12_rfps");

    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/signals/signal-001/sources");
  });

  it("throws a descriptive error on a non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("", { status: 503 }),
    );

    const { fetchPublicSignalSources } = await import("@/lib/public-signals-api");
    await expect(fetchPublicSignalSources("bad")).rejects.toThrow(
      /Failed to fetch sources for signal bad/,
    );
  });
});

// ---- fetchPublicSignals + fetchAllSignalIdsForSitemap ----

describe("fetchPublicSignals", () => {
  it("fetches /signals with the limit param and returns a page", async () => {
    const page: PublicSignalPage = { items: [SIGNAL_1], next_cursor: null };
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicSignals } = await import("@/lib/public-signals-api");
    const result = await fetchPublicSignals({ limit: 25 });

    expect(result.items).toHaveLength(1);
    const calledUrl = fetchSpy.mock.calls[0][0] as string;
    expect(calledUrl).toContain("/signals");
    expect(calledUrl).toContain("limit=25");
  });
});

describe("fetchAllSignalIdsForSitemap", () => {
  it("paginates until next_cursor is null and collects all public-type IDs", async () => {
    const page1: PublicSignalPage = {
      items: [SIGNAL_1, { ...SIGNAL_1, id: "signal-002" }],
      next_cursor: "cursor-1",
    };
    const page2: PublicSignalPage = {
      items: [{ ...SIGNAL_1, id: "signal-003" }],
      next_cursor: null,
    };

    vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(
        new Response(JSON.stringify(page1), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify(page2), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );

    const { fetchAllSignalIdsForSitemap } = await import("@/lib/public-signals-api");
    const ids = await fetchAllSignalIdsForSitemap();
    expect(ids).toEqual(["signal-001", "signal-002", "signal-003"]);
  });

  it("excludes non-public (paid-tier) signal types from the sitemap (P2)", async () => {
    const page: PublicSignalPage = {
      items: [
        SIGNAL_1, // rfp_posted — public
        { ...SIGNAL_1, id: "sig-board", signal_type: "board_agenda_item" }, // paid
        { ...SIGNAL_1, id: "sig-news", signal_type: "news_mention" }, // public
        { ...SIGNAL_1, id: "sig-budget", signal_type: "budget_approved" }, // paid
        { ...SIGNAL_1, id: "sig-grant", signal_type: "grant_awarded" }, // public
        { ...SIGNAL_1, id: "sig-leader", signal_type: "leadership_change" }, // paid
      ],
      next_cursor: null,
    };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchAllSignalIdsForSitemap } = await import("@/lib/public-signals-api");
    const ids = await fetchAllSignalIdsForSitemap();
    // Only the three public-type signals are emitted; paid-tier types are dropped.
    expect(ids).toEqual(["signal-001", "sig-news", "sig-grant"]);
  });
});

describe("isPublicSignalType", () => {
  it("allows only the late-stage public types (doc 13 §4.1, §4.6)", async () => {
    const { isPublicSignalType } = await import("@/lib/public-signals-api");
    expect(isPublicSignalType("rfp_posted")).toBe(true);
    expect(isPublicSignalType("news_mention")).toBe(true);
    expect(isPublicSignalType("grant_awarded")).toBe(true);
  });

  it("rejects paid-tier and unknown types", async () => {
    const { isPublicSignalType } = await import("@/lib/public-signals-api");
    for (const t of [
      "board_agenda_item",
      "leadership_change",
      "budget_approved",
      "contract_awarded",
      "grant_opportunity",
      "rfi_rfq",
      "strategic_plan_published",
      "open_job",
      "contract_expiring",
      "made_up_type",
    ]) {
      expect(isPublicSignalType(t)).toBe(false);
    }
  });
});
