// @vitest-environment jsdom
// C5 — Public entity profile: server component renders entity fields + source citations.
//
// Tests the /directory/[id]/page.tsx server component by mocking
// fetchPublicEntity and fetchPublicEntityChildren via vi.doMock + vi.resetModules.
// Also mocks next/navigation so notFound() doesn't crash.

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

const BASE_ENTITY: EntityRead = {
  id: "ent-abc",
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
};

const CHILD_ENTITY: EntityRead = {
  ...BASE_ENTITY,
  id: "ent-child-1",
  name: "Northshore High School",
  type: "school",
  parent_id: "ent-abc",
  enrollment: 2100,
  annual_budget_usd: null,
  primary_website: null,
  procurement_portal_url: null,
  nces_leaid: null,
  board_meeting_cadence: null,
  source_urls: [],
};

const EMPTY_CHILDREN: EntityPage = { items: [], next_cursor: null };

// ---- Helper to set up mocks and render ----

async function renderProfile(
  entityOverride: EntityRead | null = BASE_ENTITY,
  childrenOverride: EntityPage = EMPTY_CHILDREN,
) {
  // Mock next/navigation BEFORE importing the page.
  vi.doMock("next/navigation", () => ({
    notFound: vi.fn(() => {
      throw new Error("NEXT_NOT_FOUND");
    }),
    redirect: vi.fn(),
    useRouter: vi.fn(),
    usePathname: vi.fn(),
    useSearchParams: vi.fn(),
  }));

  vi.doMock("@/lib/public-entities-api", () => ({
    fetchPublicEntity: vi.fn().mockResolvedValue(entityOverride),
    fetchPublicEntityChildren: vi.fn().mockResolvedValue(childrenOverride),
    // P1 additions — provide stubs so the updated page.tsx doesn't error
    fetchPublicEntityContacts: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    fetchPublicEntitySignals: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
    fetchPublicEntities: vi.fn(),
    fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
    DIRECTORY_REVALIDATE_SECONDS: 300,
    SITEMAP_ENTITY_LIMIT: 1000,
  }));

  const { default: PublicEntityProfilePage } = await import(
    "@/app/directory/[id]/page"
  );

  const jsx = await (
    PublicEntityProfilePage as (props: {
      params: Promise<{ id: string }>;
    }) => Promise<React.ReactElement>
  )({ params: Promise.resolve({ id: "ent-abc" }) });

  return render(jsx);
}

// ---- Tests ----

describe("PublicEntityProfilePage", () => {
  it("renders the entity name as a heading", async () => {
    await renderProfile();
    expect(
      screen.getByRole("heading", { name: "Northshore School District" }),
    ).toBeTruthy();
  });

  it("renders the entity type and state", async () => {
    await renderProfile();
    expect(screen.getAllByText(/School District/).length).toBeGreaterThan(0);
    expect(screen.getByText(/WA/)).toBeTruthy();
  });

  it("renders the status badge", async () => {
    await renderProfile();
    expect(screen.getByText(/Active/i)).toBeTruthy();
  });

  it("renders enrollment and budget in the overview", async () => {
    await renderProfile();
    expect(screen.getByText(/12,011 students/)).toBeTruthy();
    expect(screen.getByText(/\$312M/)).toBeTruthy();
  });

  it("renders board meeting cadence", async () => {
    await renderProfile();
    expect(screen.getByText(/2nd & 4th Tuesday/)).toBeTruthy();
  });

  it("renders primary website as a link", async () => {
    await renderProfile();
    const link = screen.getByRole("link", { name: "https://nsd.org" });
    expect(link.getAttribute("href")).toBe("https://nsd.org");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("renders NCES LEAID", async () => {
    await renderProfile();
    expect(screen.getByText("530462")).toBeTruthy();
  });

  it("renders the Source Citations section with URLs", async () => {
    await renderProfile();
    expect(
      screen.getByRole("heading", { name: /Source Citations/i }),
    ).toBeTruthy();
    const link = screen.getByRole("link", { name: "https://nsd.org/about" });
    expect(link.getAttribute("href")).toBe("https://nsd.org/about");
    const link2 = screen.getByRole("link", { name: "https://data.nces.gov/123" });
    expect(link2.getAttribute("href")).toBe("https://data.nces.gov/123");
  });

  it("renders a back-to-directory link", async () => {
    await renderProfile();
    const backLink = screen.getByRole("link", {
      name: /Public entity directory/i,
    });
    expect(backLink.getAttribute("href")).toBe("/directory");
  });

  it("renders children when present", async () => {
    await renderProfile(BASE_ENTITY, { items: [CHILD_ENTITY], next_cursor: null });
    expect(screen.getByText("Northshore High School")).toBeTruthy();
  });

  it("child entities link to their own public profile pages", async () => {
    await renderProfile(BASE_ENTITY, { items: [CHILD_ENTITY], next_cursor: null });
    const childLink = screen.getByRole("link", { name: "Northshore High School" });
    expect(childLink.getAttribute("href")).toBe("/directory/ent-child-1");
  });

  it("shows a 'more sub-entities' note when next_cursor is set on children", async () => {
    await renderProfile(BASE_ENTITY, {
      items: [CHILD_ENTITY],
      next_cursor: "cursor-xyz",
    });
    expect(screen.getByText(/More sub-entities available/i)).toBeTruthy();
  });

  it("does not render the Source Citations section when source_urls is empty", async () => {
    const entityNoSources: EntityRead = { ...BASE_ENTITY, source_urls: [] };
    await renderProfile(entityNoSources);
    expect(
      screen.queryByRole("heading", { name: /Source Citations/i }),
    ).toBeNull();
  });

  it("renders a link to the authenticated entity page", async () => {
    await renderProfile();
    const appLink = screen.getByRole("link", { name: /view in the app/i });
    expect(appLink.getAttribute("href")).toBe("/entities/ent-abc");
  });

  it("renders a parent entity link when parent_id is set", async () => {
    const entityWithParent: EntityRead = { ...BASE_ENTITY, parent_id: "parent-001" };
    await renderProfile(entityWithParent);
    const parentLink = screen.getByRole("link", { name: /View parent entity/i });
    expect(parentLink.getAttribute("href")).toBe("/directory/parent-001");
  });

  it("renders GovernmentOrganization JSON-LD with public-safe fields only (P3)", async () => {
    // Spike internal-only fields onto the entity to prove they never leak.
    const entity: EntityRead = {
      ...BASE_ENTITY,
      kind_id: "kind-secret",
      geo_id: "geo-secret",
      attributes: { internal_note: "DO_NOT_LEAK" },
    };
    const { container } = await renderProfile(entity);

    const script = container.querySelector('script[type="application/ld+json"]');
    expect(script).not.toBeNull();
    const raw = script!.textContent ?? "{}";
    const data = JSON.parse(raw) as Record<string, unknown>;

    expect(data["@type"]).toBe("GovernmentOrganization");
    expect(data.name).toBe("Northshore School District");
    expect(data.url).toContain("/directory/ent-abc");

    // No internal fields leak into the structured data.
    for (const bad of [
      "DO_NOT_LEAK",
      "internal_note",
      "kind-secret",
      "kind_id",
      "geo-secret",
      "geo_id",
      "content_hash",
    ]) {
      expect(raw).not.toContain(bad);
    }
  });

  it("calls notFound when entity is null (404)", async () => {
    vi.doMock("next/navigation", () => ({
      notFound: vi.fn(() => {
        throw new Error("NEXT_NOT_FOUND");
      }),
      redirect: vi.fn(),
    }));

    vi.doMock("@/lib/public-entities-api", () => ({
      fetchPublicEntity: vi.fn().mockResolvedValue(null),
      fetchPublicEntityChildren: vi.fn().mockResolvedValue(EMPTY_CHILDREN),
      // P1 additions
      fetchPublicEntityContacts: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
      fetchPublicEntitySignals: vi.fn().mockResolvedValue({ items: [], next_cursor: null }),
      fetchPublicEntities: vi.fn(),
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));

    const { default: PublicEntityProfilePage } = await import(
      "@/app/directory/[id]/page"
    );

    await expect(
      (
        PublicEntityProfilePage as (props: {
          params: Promise<{ id: string }>;
        }) => Promise<React.ReactElement>
      )({ params: Promise.resolve({ id: "ent-missing" }) }),
    ).rejects.toThrow("NEXT_NOT_FOUND");
  });
});
