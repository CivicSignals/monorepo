// jsonld.ts — schema.org JSON-LD builders for the public pages (P3 SEO).
//
// These pure functions turn the *public-safe* projections (the same narrowed
// shapes the public pages already render — see public-entities-api.ts and
// public-signals-api.ts) into schema.org structured-data objects, server-rendered
// inside <script type="application/ld+json"> blocks by the page components.
//
// PUBLIC-SAFE ONLY. By construction these builders read a deliberately small set
// of fields. They never touch internal-only fields:
//   • entities: no `attributes`, no `kind_id`/`geo_id`/`parent_id` as opaque IDs
//     leaking (parent is expressed as a public canonical URL, not the raw id),
//     and no created/updated bookkeeping.
//   • signals: the input is the narrowed PublicSignalRead (no content_hash /
//     raw_document_ids / confidence / status / review_required / is_degraded /
//     details). We additionally do NOT emit the signal's own opaque `id` as a
//     value — the canonical URL is the identifier.
//   • contacts: not represented in JSON-LD at all (no emails/people PII).
//
// Schema.org type choices (documented for review):
//   • Entity profile  → GovernmentOrganization (a public-sector body).
//   • Directory index → WebSite + BreadcrumbList (a searchable site section).
//   • Signal page     → Article. An RFP / grant award / news mention is a dated,
//     titled, publisher-attributed public notice with a body; Article is the most
//     broadly-supported, defensible mapping (GovernmentService models an ongoing
//     service, and SpecialAnnouncement is scoped to time-sensitive civic
//     emergencies — neither fits a procurement/news notice). The issuing body is
//     attached as a GovernmentOrganization via `about` + `publisher`, and source
//     documents are cited via `citation` + `isBasedOn`.
//
// The objects are intentionally typed loosely (Record<string, unknown>) — JSON-LD
// is open-world and we only ever JSON.stringify them.

import type { EntityRead } from "@/lib/entities-api";
import type {
  PublicSignalRead,
  PublicSignalSource,
} from "@/lib/public-signals-api";
import { SIGNAL_TYPE_LABELS, type SignalType } from "@/lib/signals-api";

export type JsonLd = Record<string, unknown>;

const SCHEMA_CONTEXT = "https://schema.org" as const;

/**
 * Serialise a JSON-LD object for safe injection into a
 * `<script type="application/ld+json">` block via `dangerouslySetInnerHTML`.
 *
 * `JSON.stringify` alone is NOT safe here: it does not escape `<` or `/`, so a
 * public field (titles/summaries/entity names come from crawled third-party
 * content) containing `</script>` would close the script element and let the rest
 * parse as HTML — an XSS sink. We HTML-escape the angle brackets (and `&`,
 * defensively, so a literal `&lt;` in the data can't be ambiguous) into their
 * unicode escapes, which are valid inside a JSON string and render identically
 * once parsed but cannot terminate the tag. This is the same approach used by
 * mature React JSON-LD helpers.
 */
export function serializeJsonLd(data: JsonLd): string {
  return JSON.stringify(data)
    .replace(/&/g, "\\u0026")
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e");
}

/** Human label for a signal type slug (falls back to title-casing the slug). */
function signalTypeLabel(slug: string): string {
  return (
    SIGNAL_TYPE_LABELS[slug as SignalType] ??
    slug.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

/**
 * GovernmentOrganization JSON-LD for a public entity profile (/directory/[id]).
 *
 * `siteUrl` is the public site origin (no trailing slash); `id` is the entity's
 * route id (used only to build the public canonical URL — never emitted raw).
 */
export function entityJsonLd(
  entity: EntityRead,
  siteUrl: string,
  id: string,
): JsonLd {
  const canonicalUrl = `${siteUrl}/directory/${id}`;

  const ld: JsonLd = {
    "@context": SCHEMA_CONTEXT,
    "@type": "GovernmentOrganization",
    name: entity.name,
    url: canonicalUrl,
  };

  if (entity.short_name && entity.short_name !== entity.name) {
    ld.alternateName = entity.short_name;
  }

  // Public-facing official website (distinct from our canonical profile URL).
  if (entity.primary_website) {
    ld.sameAs = [entity.primary_website];
  }

  // areaServed: the geographic jurisdiction, from public location fields only.
  const areaParts = [entity.region, entity.state, entity.country].filter(
    (v): v is string => Boolean(v),
  );
  if (areaParts.length > 0) {
    ld.areaServed = {
      "@type": "AdministrativeArea",
      name: areaParts.join(", "),
      ...(entity.state ? { address: { "@type": "PostalAddress", addressRegion: entity.state, addressCountry: entity.country } } : {}),
    };
  }

  // Hierarchy: express the parent as its public canonical URL (not the raw id).
  if (entity.parent_id) {
    ld.parentOrganization = {
      "@type": "GovernmentOrganization",
      "@id": `${siteUrl}/directory/${entity.parent_id}`,
      url: `${siteUrl}/directory/${entity.parent_id}`,
    };
  }

  // Source citations: the official public URLs this profile is derived from.
  if (entity.source_urls.length > 0) {
    ld.subjectOf = entity.source_urls.map((url) => ({
      "@type": "CreativeWork",
      url,
    }));
  }

  return ld;
}

/**
 * WebSite + BreadcrumbList JSON-LD for the public directory index (/directory).
 * Returned as a @graph so a single <script> carries both nodes.
 */
export function directoryJsonLd(siteUrl: string): JsonLd {
  const directoryUrl = `${siteUrl}/directory`;
  return {
    "@context": SCHEMA_CONTEXT,
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${siteUrl}/#website`,
        name: "CivicSignals",
        url: siteUrl,
      },
      {
        "@type": "Organization",
        "@id": `${siteUrl}/#organization`,
        name: "CivicSignals",
        url: siteUrl,
      },
      {
        "@type": "CollectionPage",
        "@id": `${directoryUrl}#collection`,
        name: "Public Entity Directory",
        url: directoryUrl,
        isPartOf: { "@id": `${siteUrl}/#website` },
        about: {
          "@type": "Thing",
          name: "Government entities: school districts, cities, counties, and other public-sector bodies",
        },
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          {
            "@type": "ListItem",
            position: 1,
            name: "Home",
            item: `${siteUrl}/`,
          },
          {
            "@type": "ListItem",
            position: 2,
            name: "Public Entity Directory",
            item: directoryUrl,
          },
        ],
      },
    ],
  };
}

/**
 * Article JSON-LD for a public signal page (/s/[id]).
 *
 * `signal` MUST be the narrowed public projection (PublicSignalRead). `sources`
 * are the public-safe source citations (may be empty). `id` is used only for the
 * canonical URL — the raw id is never emitted as a JSON-LD value.
 */
export function signalJsonLd(
  signal: PublicSignalRead,
  sources: PublicSignalSource[],
  siteUrl: string,
  id: string,
): JsonLd {
  const canonicalUrl = `${siteUrl}/s/${id}`;
  const typeLabel = signalTypeLabel(signal.signal_type);

  const ld: JsonLd = {
    "@context": SCHEMA_CONTEXT,
    "@type": "Article",
    headline: signal.title,
    description: signal.summary,
    url: canonicalUrl,
    mainEntityOfPage: { "@type": "WebPage", "@id": canonicalUrl },
    // Public government notices are free to read.
    isAccessibleForFree: true,
    // Genre/category surfaces the human signal-type label (e.g. "RFP Posted").
    articleSection: typeLabel,
    // datePublished prefers the real-world occurrence; observed is the floor.
    datePublished: signal.occurred_at ?? signal.observed_at,
  };

  // The issuing/subject government body, when we resolved one (public name only).
  if (signal.entity_name) {
    const org = {
      "@type": "GovernmentOrganization",
      name: signal.entity_name,
    };
    ld.about = org;
    ld.publisher = org;
  }

  // Source documents → citations (and isBasedOn, which Google understands well).
  if (sources.length > 0) {
    const citations = sources.map((s) => ({
      "@type": "CreativeWork",
      url: s.source_url,
    }));
    ld.citation = citations;
    ld.isBasedOn = sources.map((s) => s.source_url);
  }

  return ld;
}
