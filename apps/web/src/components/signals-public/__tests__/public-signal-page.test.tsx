// @vitest-environment jsdom
// P2 — Public signal page: server component renders signal fields + source
// citations + a "Claim this in a workspace" CTA, and generateMetadata produces
// SEO tags. Mirrors the C5 public-profile.test.tsx strategy: vi.doMock +
// vi.resetModules, then import the page dynamically and await the RSC function.

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { SignalRead } from "@/lib/signals-api";
import type { PublicSignalSources } from "@/lib/public-signals-api";

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- Fixtures ----

const BASE_SIGNAL: SignalRead = {
  id: "signal-abc",
  entity_id: "ent-001",
  entity_name_raw: "Northshore School District",
  signal_type: "rfp_posted",
  recipe_id: "wa_k12_rfps",
  raw_document_ids: ["doc-001"],
  content_hash: "hash-xyz",
  occurred_at: "2026-04-15T10:00:00Z",
  observed_at: "2026-04-16T08:00:00Z",
  summary: "District seeking bids for 45 electric school buses, delivery by August 2026.",
  title: "RFP: School Transportation Fleet 2026",
  details: { due_at: "2026-06-01T17:00:00Z" },
  confidence: 0.93,
  status: "new",
  is_degraded: false,
  review_required: false,
  created_at: "2026-04-16T08:00:00Z",
};

const SOURCES: PublicSignalSources = {
  signal_id: "signal-abc",
  sources: [
    {
      document_id: "doc-001",
      source_url: "https://nsd.org/rfps/2026-transport",
      recipe_id: "wa_k12_rfps",
      fetched_at: "2026-04-16T07:30:00Z",
    },
    {
      document_id: "doc-002",
      source_url: "https://bonfirehub.com/portal/NSD123",
      recipe_id: "wa_bonfire",
      fetched_at: null,
    },
  ],
};

const EMPTY_SOURCES: PublicSignalSources = { signal_id: "signal-abc", sources: [] };

// ---- Helper: set up mocks & render ----

function mockNavigation() {
  vi.doMock("next/navigation", () => ({
    notFound: vi.fn(() => {
      throw new Error("NEXT_NOT_FOUND");
    }),
    redirect: vi.fn(),
    useRouter: vi.fn(),
    usePathname: vi.fn(),
    useSearchParams: vi.fn(),
  }));
}

async function renderSignalPage(
  signalOverride: SignalRead | null = BASE_SIGNAL,
  sourcesOverride: PublicSignalSources | "reject" = SOURCES,
) {
  mockNavigation();

  vi.doMock("@/lib/public-signals-api", () => ({
    fetchPublicSignal: vi.fn().mockResolvedValue(signalOverride),
    fetchPublicSignalSources:
      sourcesOverride === "reject"
        ? vi.fn().mockRejectedValue(new Error("sources API down"))
        : vi.fn().mockResolvedValue(sourcesOverride),
    fetchPublicSignals: vi.fn(),
    fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
    SIGNALS_REVALIDATE_SECONDS: 300,
    SITEMAP_SIGNAL_LIMIT: 1000,
  }));

  const { default: PublicSignalPage } = await import("@/app/s/[id]/page");

  const jsx = await (
    PublicSignalPage as (props: {
      params: Promise<{ id: string }>;
    }) => Promise<React.ReactElement>
  )({ params: Promise.resolve({ id: "signal-abc" }) });

  render(jsx);
}

// ---- Page render tests ----

describe("PublicSignalPage — render", () => {
  it("renders the signal title as an h1", async () => {
    await renderSignalPage();
    expect(
      screen.getByRole("heading", {
        name: "RFP: School Transportation Fleet 2026",
        level: 1,
      }),
    ).toBeTruthy();
  });

  it("renders the signal type label", async () => {
    await renderSignalPage();
    expect(screen.getAllByText(/RFP Posted/).length).toBeGreaterThan(0);
  });

  it("renders the summary", async () => {
    await renderSignalPage();
    expect(
      screen.getByText(/District seeking bids for 45 electric school buses/),
    ).toBeTruthy();
  });

  it("renders the subject entity name", async () => {
    await renderSignalPage();
    expect(screen.getAllByText(/Northshore School District/).length).toBeGreaterThan(0);
  });

  it("renders an observed time element", async () => {
    await renderSignalPage();
    const timeEl = screen.getByRole("time");
    expect(timeEl.getAttribute("dateTime")).toBe("2026-04-16T08:00:00Z");
  });

  it("renders the Source Citations section with each source URL", async () => {
    await renderSignalPage();
    expect(
      screen.getByRole("heading", { name: /Source Citations/i }),
    ).toBeTruthy();
    const link1 = screen.getByRole("link", {
      name: "https://nsd.org/rfps/2026-transport",
    });
    expect(link1.getAttribute("href")).toBe("https://nsd.org/rfps/2026-transport");
    expect(link1.getAttribute("rel")).toContain("noopener");
    const link2 = screen.getByRole("link", {
      name: "https://bonfirehub.com/portal/NSD123",
    });
    expect(link2.getAttribute("href")).toBe("https://bonfirehub.com/portal/NSD123");
  });

  it("does not render Source Citations when there are no sources", async () => {
    await renderSignalPage(BASE_SIGNAL, EMPTY_SOURCES);
    expect(
      screen.queryByRole("heading", { name: /Source Citations/i }),
    ).toBeNull();
  });

  it("still renders the signal when the sources fetch fails (graceful degradation)", async () => {
    await renderSignalPage(BASE_SIGNAL, "reject");
    expect(
      screen.getByRole("heading", {
        name: "RFP: School Transportation Fleet 2026",
        level: 1,
      }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("heading", { name: /Source Citations/i }),
    ).toBeNull();
  });

  it("renders the 'Claim this in a workspace' CTA linking to signup", async () => {
    await renderSignalPage();
    const cta = screen.getByRole("link", { name: /Claim this in a workspace/i });
    expect(cta.getAttribute("href")).toBe("/signup");
  });

  it("renders a back-to-directory breadcrumb", async () => {
    await renderSignalPage();
    const back = screen.getByRole("link", { name: /public directory/i });
    expect(back.getAttribute("href")).toBe("/directory");
  });

  it("calls notFound() when the signal is null (404)", async () => {
    mockNavigation();
    vi.doMock("@/lib/public-signals-api", () => ({
      fetchPublicSignal: vi.fn().mockResolvedValue(null),
      fetchPublicSignalSources: vi.fn().mockResolvedValue(EMPTY_SOURCES),
      fetchPublicSignals: vi.fn(),
      fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
      SIGNALS_REVALIDATE_SECONDS: 300,
      SITEMAP_SIGNAL_LIMIT: 1000,
    }));

    const { default: PublicSignalPage } = await import("@/app/s/[id]/page");
    await expect(
      (
        PublicSignalPage as (props: {
          params: Promise<{ id: string }>;
        }) => Promise<React.ReactElement>
      )({ params: Promise.resolve({ id: "missing" }) }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });
});

// ---- generateMetadata tests ----

describe("PublicSignalPage — generateMetadata", () => {
  async function getMeta(signal: SignalRead | null) {
    mockNavigation();
    vi.doMock("@/lib/public-signals-api", () => ({
      fetchPublicSignal: vi.fn().mockResolvedValue(signal),
      fetchPublicSignalSources: vi.fn().mockResolvedValue(EMPTY_SOURCES),
      fetchPublicSignals: vi.fn(),
      fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
      SIGNALS_REVALIDATE_SECONDS: 300,
      SITEMAP_SIGNAL_LIMIT: 1000,
    }));
    const { generateMetadata } = await import("@/app/s/[id]/page");
    return (
      generateMetadata as (props: {
        params: Promise<{ id: string }>;
      }) => Promise<import("next").Metadata>
    )({ params: Promise.resolve({ id: "signal-abc" }) });
  }

  it("sets a title containing the signal title", async () => {
    const meta = await getMeta(BASE_SIGNAL);
    expect(meta.title).toContain("RFP: School Transportation Fleet 2026");
  });

  it("sets a canonical URL under /s/", async () => {
    const meta = await getMeta(BASE_SIGNAL);
    expect(meta.alternates?.canonical).toContain("/s/signal-abc");
  });

  it("sets openGraph and twitter metadata", async () => {
    const meta = await getMeta(BASE_SIGNAL);
    expect(meta.openGraph?.title).toContain("RFP: School Transportation Fleet 2026");
    expect((meta.twitter as { card: string }).card).toBe("summary");
  });

  it("returns a safe fallback when the signal is null (404)", async () => {
    const meta = await getMeta(null);
    expect(meta.title).toContain("Not Found");
    expect(meta.description).toBeTruthy();
  });
});
