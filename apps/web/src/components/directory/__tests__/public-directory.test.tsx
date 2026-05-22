// @vitest-environment jsdom
// C5 — Public directory: server component renders entity data + source citations.
//
// Strategy: The /directory/page.tsx is a Next.js Server Component. We test it
// by mocking fetchPublicEntities via vi.doMock, resetting module state between
// tests, and awaiting the server component function directly.
//
// The public-entities-api functions are also unit-tested directly.

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { EntityRead, EntityPage } from "@/lib/entities-api";

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- Fixtures ----

function makeEntity(over: Partial<EntityRead> = {}): EntityRead {
  return {
    id: "ent-001",
    type: "school_district",
    status: "active",
    name: "Northshore School District",
    short_name: "Northshore SD",
    country: "US",
    state: "WA",
    region: null,
    kind_id: null,
    geo_id: null,
    parent_id: null,
    nces_leaid: "530462",
    ipeds_unitid: null,
    census_gid: null,
    population: null,
    enrollment: 12011,
    annual_budget_usd: 312_000_000,
    primary_website: "https://nsd.org",
    procurement_portal_url: "https://bonfirehub.com/portal/?tab=openOpps&portalID=NSD123",
    board_meeting_cadence: "2nd & 4th Tuesday",
    attributes: {},
    source_urls: ["https://nsd.org/about", "https://data.nces.gov/123"],
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...over,
  };
}

// ---- public-entities-api unit tests ----

describe("public-entities-api: fetchPublicEntities", () => {
  it("calls the /entities endpoint and returns EntityPage", async () => {
    const entity = makeEntity();
    const page: EntityPage = { items: [entity], next_cursor: null };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicEntities } = await import("@/lib/public-entities-api");
    const result = await fetchPublicEntities({ status: "active", limit: 25 });

    expect(result.items).toHaveLength(1);
    expect(result.items[0].name).toBe("Northshore School District");
    expect(result.next_cursor).toBeNull();
  });

  it("throws on non-ok response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("", { status: 500 }),
    );

    const { fetchPublicEntities } = await import("@/lib/public-entities-api");
    await expect(fetchPublicEntities({})).rejects.toThrow(/Failed to fetch entities/);
  });
});

describe("public-entities-api: fetchPublicEntity", () => {
  it("returns the entity on 200", async () => {
    const entity = makeEntity();
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(entity), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchPublicEntity } = await import("@/lib/public-entities-api");
    const result = await fetchPublicEntity("ent-001");
    expect(result).not.toBeNull();
    expect(result?.name).toBe("Northshore School District");
  });

  it("returns null on 404", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("", { status: 404 }),
    );

    const { fetchPublicEntity } = await import("@/lib/public-entities-api");
    const result = await fetchPublicEntity("ent-missing");
    expect(result).toBeNull();
  });

  it("throws on 500", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("", { status: 500 }),
    );

    const { fetchPublicEntity } = await import("@/lib/public-entities-api");
    await expect(fetchPublicEntity("ent-bad")).rejects.toThrow(/Failed to fetch entity/);
  });
});

describe("public-entities-api: fetchAllEntityIdsForSitemap", () => {
  it("paginates through multiple pages up to SITEMAP_ENTITY_LIMIT", async () => {
    const entity1 = makeEntity({ id: "ent-001" });
    const entity2 = makeEntity({ id: "ent-002", name: "Plano ISD" });
    const entity3 = makeEntity({ id: "ent-003", name: "Frisco ISD" });

    const page1: EntityPage = { items: [entity1, entity2], next_cursor: "cursor-p2" };
    const page2: EntityPage = { items: [entity3], next_cursor: null };

    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
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

    const { fetchAllEntityIdsForSitemap } = await import("@/lib/public-entities-api");
    const ids = await fetchAllEntityIdsForSitemap();

    expect(ids).toEqual(["ent-001", "ent-002", "ent-003"]);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
  });

  it("stops when next_cursor is null", async () => {
    const entity = makeEntity({ id: "ent-only" });
    const page: EntityPage = { items: [entity], next_cursor: null };

    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(page), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const { fetchAllEntityIdsForSitemap } = await import("@/lib/public-entities-api");
    const ids = await fetchAllEntityIdsForSitemap();
    expect(ids).toEqual(["ent-only"]);
  });
});

// ---- Public directory page rendering tests ----

describe("Public directory page: rendering", () => {
  it("renders entity name and state when API returns data", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchPublicEntities: vi.fn().mockResolvedValue({
        items: [makeEntity({ id: "ent-001" })],
        next_cursor: null,
      }),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));

    const { default: PublicDirectoryPage } = await import("@/app/directory/page");
    const jsx = await (PublicDirectoryPage as () => Promise<React.ReactElement>)();
    render(jsx);

    expect(screen.getByRole("heading", { name: /public entity directory/i })).toBeTruthy();
    expect(screen.getByText("Northshore School District")).toBeTruthy();
    expect(screen.getByText(/WA/)).toBeTruthy();
  });

  it("shows source citation on directory rows", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchPublicEntities: vi.fn().mockResolvedValue({
        items: [makeEntity({ id: "ent-001" })],
        next_cursor: null,
      }),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));

    const { default: PublicDirectoryPage } = await import("@/app/directory/page");
    const jsx = await (PublicDirectoryPage as () => Promise<React.ReactElement>)();
    render(jsx);

    // The first source_url should appear as a visible link in the directory row.
    expect(screen.getByText("https://nsd.org/about")).toBeTruthy();
  });

  it("shows empty state when API returns no entities", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchPublicEntities: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));

    const { default: PublicDirectoryPage } = await import("@/app/directory/page");
    const jsx = await (PublicDirectoryPage as () => Promise<React.ReactElement>)();
    render(jsx);

    expect(screen.getByText(/No entities available yet/i)).toBeTruthy();
  });

  it("shows error state when API throws", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchPublicEntities: vi.fn().mockRejectedValue(new Error("API down")),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));

    const { default: PublicDirectoryPage } = await import("@/app/directory/page");
    const jsx = await (PublicDirectoryPage as () => Promise<React.ReactElement>)();
    render(jsx);

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/temporarily unavailable/i)).toBeTruthy();
  });

  it("renders a well-formed JSON-LD @graph (WebSite + BreadcrumbList) (P3)", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchPublicEntities: vi.fn().mockResolvedValue({
        items: [makeEntity({ id: "ent-001" })],
        next_cursor: null,
      }),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));

    const { default: PublicDirectoryPage } = await import("@/app/directory/page");
    const jsx = await (PublicDirectoryPage as () => Promise<React.ReactElement>)();
    const { container } = render(jsx);

    const script = container.querySelector(
      'script[type="application/ld+json"]',
    );
    expect(script).not.toBeNull();
    const data = JSON.parse(script!.textContent ?? "{}");
    const types = (data["@graph"] as Array<{ "@type": string }>).map(
      (n) => n["@type"],
    );
    expect(types).toContain("WebSite");
    expect(types).toContain("BreadcrumbList");
  });

  it("directory rows link to /directory/[id]", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchPublicEntities: vi.fn().mockResolvedValue({
        items: [makeEntity({ id: "ent-001" })],
        next_cursor: null,
      }),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));

    const { default: PublicDirectoryPage } = await import("@/app/directory/page");
    const jsx = await (PublicDirectoryPage as () => Promise<React.ReactElement>)();
    render(jsx);

    const link = screen.getByRole("link", { name: "Northshore School District" });
    expect(link.getAttribute("href")).toBe("/directory/ent-001");
  });
});
