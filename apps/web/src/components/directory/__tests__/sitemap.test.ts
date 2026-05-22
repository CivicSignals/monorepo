// C5 — Sitemap: returns expected URLs including static pages and entity profiles.
//
// Tests the sitemap() function from src/app/sitemap.ts.
// Mocks fetchAllEntityIdsForSitemap via vi.doMock + vi.resetModules.

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const SITE_URL = "https://civicsignals.io";

describe("sitemap()", () => {
  it("includes the home page, pricing page, and /directory index as static entries", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue([]),
      fetchPublicEntities: vi.fn(),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));
    vi.doMock("@/lib/public-signals-api", () => ({
      fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
      SITEMAP_SIGNAL_LIMIT: 1000,
    }));

    const { default: sitemap } = await import("@/app/sitemap");
    const entries = await sitemap();

    const urls = entries.map((e) => e.url);
    expect(urls).toContain(`${SITE_URL}/`);
    expect(urls).toContain(`${SITE_URL}/pricing`);
    expect(urls).toContain(`${SITE_URL}/directory`);
  });

  it("includes /directory/[id] URLs for each entity ID", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchAllEntityIdsForSitemap: vi
        .fn()
        .mockResolvedValue(["ent-001", "ent-002", "ent-003"]),
      fetchPublicEntities: vi.fn(),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));
    vi.doMock("@/lib/public-signals-api", () => ({
      fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
      SITEMAP_SIGNAL_LIMIT: 1000,
    }));

    const { default: sitemap } = await import("@/app/sitemap");
    const entries = await sitemap();

    const urls = entries.map((e) => e.url);
    expect(urls).toContain(`${SITE_URL}/directory/ent-001`);
    expect(urls).toContain(`${SITE_URL}/directory/ent-002`);
    expect(urls).toContain(`${SITE_URL}/directory/ent-003`);
  });

  it("does not include /entities/* routes (auth-only pages)", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue(["ent-001"]),
      fetchPublicEntities: vi.fn(),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));
    vi.doMock("@/lib/public-signals-api", () => ({
      fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
      SITEMAP_SIGNAL_LIMIT: 1000,
    }));

    const { default: sitemap } = await import("@/app/sitemap");
    const entries = await sitemap();

    const urls = entries.map((e) => e.url);
    const entitiesUrls = urls.filter((u) => u.includes("/entities/"));
    expect(entitiesUrls).toHaveLength(0);
  });

  it("returns only static pages when fetchAllEntityIdsForSitemap throws", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchAllEntityIdsForSitemap: vi.fn().mockRejectedValue(new Error("API down")),
      fetchPublicEntities: vi.fn(),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));
    vi.doMock("@/lib/public-signals-api", () => ({
      fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
      SITEMAP_SIGNAL_LIMIT: 1000,
    }));

    const { default: sitemap } = await import("@/app/sitemap");
    const entries = await sitemap();

    const urls = entries.map((e) => e.url);
    // Static pages still present
    expect(urls).toContain(`${SITE_URL}/`);
    expect(urls).toContain(`${SITE_URL}/directory`);
    // No entity-specific pages when API fails (i.e., no /directory/XXX paths)
    const entitySpecificUrls = urls.filter((u) => {
      const path = u.replace(SITE_URL, "");
      return path.startsWith("/directory/") && path.length > "/directory/".length;
    });
    expect(entitySpecificUrls).toHaveLength(0);
  });

  it("sets a lastModified date for each entry", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue(["ent-001"]),
      fetchPublicEntities: vi.fn(),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));
    vi.doMock("@/lib/public-signals-api", () => ({
      fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
      SITEMAP_SIGNAL_LIMIT: 1000,
    }));

    const { default: sitemap } = await import("@/app/sitemap");
    const entries = await sitemap();

    for (const entry of entries) {
      expect(entry.lastModified).toBeInstanceOf(Date);
    }
  });

  it("sets priority 0.7 for entity profile pages", async () => {
    vi.doMock("@/lib/public-entities-api", () => ({
      fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue(["ent-001"]),
      fetchPublicEntities: vi.fn(),
      fetchPublicEntity: vi.fn(),
      fetchPublicEntityChildren: vi.fn(),
      DIRECTORY_REVALIDATE_SECONDS: 300,
      SITEMAP_ENTITY_LIMIT: 1000,
    }));
    vi.doMock("@/lib/public-signals-api", () => ({
      fetchAllSignalIdsForSitemap: vi.fn().mockResolvedValue([]),
      SITEMAP_SIGNAL_LIMIT: 1000,
    }));

    const { default: sitemap } = await import("@/app/sitemap");
    const entries = await sitemap();

    const entityEntry = entries.find((e) =>
      e.url.includes("/directory/ent-001"),
    );
    expect(entityEntry?.priority).toBe(0.7);
  });
});
