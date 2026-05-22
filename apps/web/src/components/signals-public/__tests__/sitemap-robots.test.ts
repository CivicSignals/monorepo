// P2 — sitemap + robots: public signal pages (/s/[id]) are enumerated in the
// sitemap and allowed by robots.txt. Complements the C5 sitemap.test.ts (which
// covers the entity side); here we assert the signal additions.

import { describe, expect, it, vi, afterEach, beforeEach } from "vitest";

beforeEach(() => {
  vi.resetModules();
});

afterEach(() => {
  vi.restoreAllMocks();
});

const SITE_URL = "https://civicsignals.io";

function mockApis(opts: {
  entityIds?: string[];
  signalIds?: string[];
  rejectSignals?: boolean;
}) {
  vi.doMock("@/lib/public-entities-api", () => ({
    fetchAllEntityIdsForSitemap: vi.fn().mockResolvedValue(opts.entityIds ?? []),
    fetchPublicEntities: vi.fn(),
    fetchPublicEntity: vi.fn(),
    fetchPublicEntityChildren: vi.fn(),
    DIRECTORY_REVALIDATE_SECONDS: 300,
    SITEMAP_ENTITY_LIMIT: 1000,
  }));
  vi.doMock("@/lib/public-signals-api", () => ({
    fetchAllSignalIdsForSitemap: opts.rejectSignals
      ? vi.fn().mockRejectedValue(new Error("signals API down"))
      : vi.fn().mockResolvedValue(opts.signalIds ?? []),
    SITEMAP_SIGNAL_LIMIT: 1000,
  }));
}

describe("sitemap() — public signal pages (P2)", () => {
  it("includes /s/[id] URLs for each signal ID", async () => {
    mockApis({ signalIds: ["sig-001", "sig-002"] });
    const { default: sitemap } = await import("@/app/sitemap");
    const urls = (await sitemap()).map((e) => e.url);
    expect(urls).toContain(`${SITE_URL}/s/sig-001`);
    expect(urls).toContain(`${SITE_URL}/s/sig-002`);
  });

  it("sets priority 0.6 for signal pages", async () => {
    mockApis({ signalIds: ["sig-001"] });
    const { default: sitemap } = await import("@/app/sitemap");
    const entry = (await sitemap()).find((e) => e.url.includes("/s/sig-001"));
    expect(entry?.priority).toBe(0.6);
  });

  it("still includes entity + static pages when the signal walk throws", async () => {
    mockApis({ entityIds: ["ent-001"], rejectSignals: true });
    const { default: sitemap } = await import("@/app/sitemap");
    const urls = (await sitemap()).map((e) => e.url);
    // Static + entity pages survive a signal-source failure.
    expect(urls).toContain(`${SITE_URL}/`);
    expect(urls).toContain(`${SITE_URL}/directory/ent-001`);
    // No signal pages were added.
    expect(urls.filter((u) => u.includes("/s/"))).toHaveLength(0);
  });

  it("does not include the authenticated /feed route", async () => {
    mockApis({ signalIds: ["sig-001"] });
    const { default: sitemap } = await import("@/app/sitemap");
    const urls = (await sitemap()).map((e) => e.url);
    expect(urls.some((u) => u.endsWith("/feed"))).toBe(false);
  });
});

describe("robots() — public signal pages (P2)", () => {
  it("allows /s/ and disallows the authenticated /feed", async () => {
    const { default: robots } = await import("@/app/robots");
    const result = robots();
    const rule = Array.isArray(result.rules) ? result.rules[0] : result.rules;
    const allow = rule?.allow;
    const disallow = rule?.disallow;
    const allowArr = Array.isArray(allow) ? allow : allow ? [allow] : [];
    const disallowArr = Array.isArray(disallow) ? disallow : disallow ? [disallow] : [];
    expect(allowArr).toContain("/s/");
    expect(disallowArr).toContain("/feed");
  });
});

describe("robots() — Crawl-delay (P4)", () => {
  it("sets a positive Crawl-delay on the general (*) rule", async () => {
    const { default: robots } = await import("@/app/robots");
    const result = robots();
    const rule = Array.isArray(result.rules) ? result.rules[0] : result.rules;
    // Next emits `Crawl-delay: <n>` from the per-rule `crawlDelay` field; assert
    // it is present and positive so polite crawlers pace themselves (P4).
    expect(rule?.userAgent).toBe("*");
    expect(typeof rule?.crawlDelay).toBe("number");
    expect(rule?.crawlDelay ?? 0).toBeGreaterThan(0);
  });

  it("keeps the public crawl paths allowed alongside the Crawl-delay", async () => {
    const { default: robots } = await import("@/app/robots");
    const result = robots();
    const rule = Array.isArray(result.rules) ? result.rules[0] : result.rules;
    const allow = rule?.allow;
    const allowArr = Array.isArray(allow) ? allow : allow ? [allow] : [];
    // /directory/* and /s/* stay crawlable; auth/api stay disallowed.
    expect(allowArr).toContain("/directory/");
    expect(allowArr).toContain("/s/");
    const disallow = rule?.disallow;
    const disallowArr = Array.isArray(disallow) ? disallow : disallow ? [disallow] : [];
    expect(disallowArr).toContain("/api/");
    expect(disallowArr).toContain("/feed");
  });
});
