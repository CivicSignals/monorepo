// P3 — JSON-LD builders: assert each public page's structured data has the right
// schema.org @type and key fields, is well-formed (round-trips through JSON), and
// — critically — that no internal-only field ever leaks into the output.

import { describe, expect, it } from "vitest";
import { entityJsonLd, directoryJsonLd, signalJsonLd } from "@/lib/jsonld";
import type { EntityRead } from "@/lib/entities-api";
import type {
  PublicSignalRead,
  PublicSignalSource,
} from "@/lib/public-signals-api";

const SITE_URL = "https://civicsignals.io";

// ---- Fixtures (mirror the public projections the pages already render) ----

const ENTITY: EntityRead = {
  id: "ent-abc",
  type: "school_district",
  status: "active",
  name: "Northshore School District",
  short_name: "Northshore SD",
  country: "US",
  state: "WA",
  region: "King County",
  kind_id: "kind-secret-001",
  geo_id: "geo-secret-002",
  parent_id: "parent-xyz",
  nces_leaid: "530462",
  ipeds_unitid: null,
  census_gid: null,
  population: null,
  enrollment: 12011,
  annual_budget_usd: 312_000_000,
  primary_website: "https://nsd.org",
  procurement_portal_url: "https://bonfirehub.com/portal/NSD123",
  board_meeting_cadence: "2nd & 4th Tuesday",
  attributes: { internal_note: "DO_NOT_LEAK", risk_score: 0.91 },
  source_urls: ["https://nsd.org/about", "https://data.nces.gov/123"],
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
};

const SIGNAL: PublicSignalRead = {
  id: "signal-abc",
  signal_type: "rfp_posted",
  title: "RFP: School Transportation Fleet 2026",
  summary: "District seeking bids for 45 electric school buses.",
  entity_name: "Northshore School District",
  occurred_at: "2026-04-15T10:00:00Z",
  observed_at: "2026-04-16T08:00:00Z",
};

const SOURCES: PublicSignalSource[] = [
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
];

/** Serialise then parse — proves the object is JSON-safe — and return text+obj. */
function roundTrip(obj: unknown): { text: string; parsed: unknown } {
  const text = JSON.stringify(obj);
  return { text, parsed: JSON.parse(text) };
}

// Internal/opaque fields that must NEVER appear anywhere in JSON-LD output.
const FORBIDDEN_SUBSTRINGS = [
  "DO_NOT_LEAK",
  "internal_note",
  "risk_score",
  "kind-secret-001",
  "kind_id",
  "geo-secret-002",
  "geo_id",
  "content_hash",
  "raw_document_ids",
  "confidence",
  "review_required",
  "is_degraded",
  "document_id",
  "doc-001",
  "recipe_id",
  "wa_k12_rfps",
];

function assertNoLeak(text: string) {
  for (const bad of FORBIDDEN_SUBSTRINGS) {
    expect(text).not.toContain(bad);
  }
}

/**
 * The opaque id is allowed to appear ONLY inside the canonical URL slug (the
 * standard, public way to address the page) — never as a bare identifier value
 * (e.g. an `identifier`/`@id` of just the raw id). Assert it never appears outside
 * a `/<segment>/<id>` URL context.
 */
function assertIdOnlyInUrl(text: string, id: string, urlSegment: string) {
  // Remove every legitimate occurrence (the canonical URL slug), then confirm
  // the id no longer appears anywhere.
  const stripped = text.split(`${urlSegment}/${id}`).join(`${urlSegment}/`);
  expect(stripped).not.toContain(id);
}

// ---- entityJsonLd ----

describe("entityJsonLd", () => {
  it("is GovernmentOrganization with name + canonical url", () => {
    const ld = entityJsonLd(ENTITY, SITE_URL, ENTITY.id) as Record<string, unknown>;
    expect(ld["@context"]).toBe("https://schema.org");
    expect(ld["@type"]).toBe("GovernmentOrganization");
    expect(ld.name).toBe("Northshore School District");
    expect(ld.url).toBe(`${SITE_URL}/directory/ent-abc`);
  });

  it("emits alternateName, sameAs (official site), and areaServed", () => {
    const ld = entityJsonLd(ENTITY, SITE_URL, ENTITY.id) as Record<string, unknown>;
    expect(ld.alternateName).toBe("Northshore SD");
    expect(ld.sameAs).toEqual(["https://nsd.org"]);
    const area = ld.areaServed as Record<string, unknown>;
    expect(area["@type"]).toBe("AdministrativeArea");
    expect(area.name).toContain("WA");
  });

  it("expresses the parent as a public canonical URL (not a raw id value)", () => {
    const ld = entityJsonLd(ENTITY, SITE_URL, ENTITY.id) as Record<string, unknown>;
    const parent = ld.parentOrganization as Record<string, unknown>;
    expect(parent["@type"]).toBe("GovernmentOrganization");
    expect(parent.url).toBe(`${SITE_URL}/directory/parent-xyz`);
  });

  it("cites source URLs via subjectOf", () => {
    const ld = entityJsonLd(ENTITY, SITE_URL, ENTITY.id) as Record<string, unknown>;
    const subjectOf = ld.subjectOf as Array<Record<string, unknown>>;
    expect(subjectOf).toHaveLength(2);
    expect(subjectOf[0].url).toBe("https://nsd.org/about");
  });

  it("is well-formed JSON and leaks no internal fields", () => {
    const { text } = roundTrip(entityJsonLd(ENTITY, SITE_URL, ENTITY.id));
    assertNoLeak(text);
    // The entity id appears only inside the canonical /directory/<id> URL.
    assertIdOnlyInUrl(text, ENTITY.id, "/directory");
  });

  it("omits optional blocks when fields are absent", () => {
    const minimal: EntityRead = {
      ...ENTITY,
      short_name: null,
      primary_website: null,
      region: null,
      state: null,
      parent_id: null,
      source_urls: [],
    };
    const ld = entityJsonLd(minimal, SITE_URL, minimal.id) as Record<string, unknown>;
    expect(ld.alternateName).toBeUndefined();
    expect(ld.sameAs).toBeUndefined();
    expect(ld.parentOrganization).toBeUndefined();
    expect(ld.subjectOf).toBeUndefined();
    // country still present, so areaServed survives
    expect(ld.areaServed).toBeDefined();
  });
});

// ---- directoryJsonLd ----

describe("directoryJsonLd", () => {
  it("emits a @graph with WebSite, Organization, CollectionPage, BreadcrumbList", () => {
    const ld = directoryJsonLd(SITE_URL) as Record<string, unknown>;
    expect(ld["@context"]).toBe("https://schema.org");
    const graph = ld["@graph"] as Array<Record<string, unknown>>;
    const types = graph.map((n) => n["@type"]);
    expect(types).toContain("WebSite");
    expect(types).toContain("Organization");
    expect(types).toContain("CollectionPage");
    expect(types).toContain("BreadcrumbList");
  });

  it("breadcrumb points Home → Public Entity Directory", () => {
    const ld = directoryJsonLd(SITE_URL) as Record<string, unknown>;
    const graph = ld["@graph"] as Array<Record<string, unknown>>;
    const crumb = graph.find((n) => n["@type"] === "BreadcrumbList")!;
    const items = crumb.itemListElement as Array<Record<string, unknown>>;
    expect(items[0].item).toBe(`${SITE_URL}/`);
    expect(items[1].item).toBe(`${SITE_URL}/directory`);
  });

  it("is well-formed JSON", () => {
    const { parsed } = roundTrip(directoryJsonLd(SITE_URL));
    expect(parsed).toBeTruthy();
  });
});

// ---- signalJsonLd ----

describe("signalJsonLd", () => {
  it("is an Article with headline, description, datePublished", () => {
    const ld = signalJsonLd(SIGNAL, SOURCES, SITE_URL, SIGNAL.id) as Record<string, unknown>;
    expect(ld["@context"]).toBe("https://schema.org");
    expect(ld["@type"]).toBe("Article");
    expect(ld.headline).toBe("RFP: School Transportation Fleet 2026");
    expect(ld.description).toBe(SIGNAL.summary);
    // datePublished prefers occurred_at
    expect(ld.datePublished).toBe("2026-04-15T10:00:00Z");
    expect(ld.url).toBe(`${SITE_URL}/s/signal-abc`);
    expect(ld.isAccessibleForFree).toBe(true);
    expect(ld.articleSection).toBe("RFP Posted");
  });

  it("attaches the issuing entity as about + publisher GovernmentOrganization", () => {
    const ld = signalJsonLd(SIGNAL, SOURCES, SITE_URL, SIGNAL.id) as Record<string, unknown>;
    const about = ld.about as Record<string, unknown>;
    expect(about["@type"]).toBe("GovernmentOrganization");
    expect(about.name).toBe("Northshore School District");
    const publisher = ld.publisher as Record<string, unknown>;
    expect(publisher.name).toBe("Northshore School District");
  });

  it("cites the source URLs (and not internal source ids)", () => {
    const ld = signalJsonLd(SIGNAL, SOURCES, SITE_URL, SIGNAL.id) as Record<string, unknown>;
    const citation = ld.citation as Array<Record<string, unknown>>;
    expect(citation.map((c) => c.url)).toEqual([
      "https://nsd.org/rfps/2026-transport",
      "https://bonfirehub.com/portal/NSD123",
    ]);
    expect(ld.isBasedOn).toEqual([
      "https://nsd.org/rfps/2026-transport",
      "https://bonfirehub.com/portal/NSD123",
    ]);
  });

  it("falls back to observed_at when occurred_at is null, and omits citations when no sources", () => {
    const noOccur: PublicSignalRead = { ...SIGNAL, occurred_at: null };
    const ld = signalJsonLd(noOccur, [], SITE_URL, noOccur.id) as Record<string, unknown>;
    expect(ld.datePublished).toBe("2026-04-16T08:00:00Z");
    expect(ld.citation).toBeUndefined();
    expect(ld.isBasedOn).toBeUndefined();
  });

  it("is well-formed JSON and leaks no internal fields", () => {
    const { text } = roundTrip(signalJsonLd(SIGNAL, SOURCES, SITE_URL, SIGNAL.id));
    assertNoLeak(text);
    // The signal id appears only inside the canonical /s/<id> URL — never as a
    // bare identifier value.
    assertIdOnlyInUrl(text, SIGNAL.id, "/s");
  });
});
