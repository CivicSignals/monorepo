// sitemap.ts — Next.js dynamic sitemap (C5).
//
// Generates a sitemap.xml enumerating:
//   1. Static public pages (home, pricing, /directory index).
//   2. Public entity profile pages (/directory/[id]) up to SITEMAP_ENTITY_LIMIT.
//
// The sitemap is regenerated on each build and then periodically revalidated
// (Next.js ISR). For very large entity counts it stays within reasonable size
// by capping at SITEMAP_ENTITY_LIMIT (1000 entities by default).
//
// The authenticated /entities/* pages are intentionally excluded — they require
// a session and are not crawlable by design.
//
// TODO P4: rate-limit the paginated API calls in fetchAllEntityIdsForSitemap.
// TODO P3: add <changefreq> and <priority> per entity once we track update dates.

import type { MetadataRoute } from "next";
import { fetchAllEntityIdsForSitemap } from "@/lib/public-entities-api";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://civicsignals.io";

// Revalidate sitemap every hour (entity list changes slowly).
export const revalidate = 3600;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  // Static pages that are always present and publicly accessible.
  const staticPages: MetadataRoute.Sitemap = [
    {
      url: `${SITE_URL}/`,
      lastModified: new Date(),
      changeFrequency: "weekly",
      priority: 1,
    },
    {
      url: `${SITE_URL}/pricing`,
      lastModified: new Date(),
      changeFrequency: "monthly",
      priority: 0.8,
    },
    {
      url: `${SITE_URL}/directory`,
      lastModified: new Date(),
      changeFrequency: "daily",
      priority: 0.9,
    },
  ];

  // Fetch entity IDs for the dynamic /directory/[id] pages.
  // Falls back to an empty list if the API is unavailable (sitemap still valid).
  let entityIds: string[] = [];
  try {
    entityIds = await fetchAllEntityIdsForSitemap();
  } catch {
    // Non-fatal: return static pages only.
    return staticPages;
  }

  const entityPages: MetadataRoute.Sitemap = entityIds.map((id) => ({
    url: `${SITE_URL}/directory/${id}`,
    lastModified: new Date(),
    changeFrequency: "weekly" as const,
    priority: 0.7,
  }));

  return [...staticPages, ...entityPages];
}
