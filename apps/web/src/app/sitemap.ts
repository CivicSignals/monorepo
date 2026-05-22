// sitemap.ts — Next.js dynamic sitemap (C5, P2).
//
// Generates a sitemap.xml enumerating:
//   1. Static public pages (home, pricing, /directory index).
//   2. Public entity profile pages (/directory/[id]) up to SITEMAP_ENTITY_LIMIT.
//   3. Public signal pages (/s/[id]) up to SITEMAP_SIGNAL_LIMIT (P2).
//
// The sitemap is regenerated on each build and then periodically revalidated
// (Next.js ISR). For very large counts it stays within reasonable size by capping
// each list (1000 entities + 1000 signals by default).
//
// The authenticated /entities/* and workspace /feed pages are intentionally
// excluded — they require a session and are not crawlable by design. Each list is
// fetched independently so one failing source doesn't drop the others.
//
// TODO P4: rate-limit the paginated API calls in the sitemap walks.
// TODO P3: add <changefreq> and <priority> tuning once we track update dates.

import type { MetadataRoute } from "next";
import { fetchAllEntityIdsForSitemap } from "@/lib/public-entities-api";
import { fetchAllSignalIdsForSitemap } from "@/lib/public-signals-api";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://civicsignals.io";

// Revalidate sitemap every hour (entity + signal lists change slowly).
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

  // Fetch entity IDs for /directory/[id]. Non-fatal: an empty list on failure.
  let entityIds: string[] = [];
  try {
    entityIds = await fetchAllEntityIdsForSitemap();
  } catch {
    entityIds = [];
  }

  // Fetch signal IDs for /s/[id] (P2). Non-fatal: an empty list on failure.
  let signalIds: string[] = [];
  try {
    signalIds = await fetchAllSignalIdsForSitemap();
  } catch {
    signalIds = [];
  }

  const entityPages: MetadataRoute.Sitemap = entityIds.map((id) => ({
    url: `${SITE_URL}/directory/${id}`,
    lastModified: new Date(),
    changeFrequency: "weekly" as const,
    priority: 0.7,
  }));

  const signalPages: MetadataRoute.Sitemap = signalIds.map((id) => ({
    url: `${SITE_URL}/s/${id}`,
    lastModified: new Date(),
    changeFrequency: "weekly" as const,
    priority: 0.6,
  }));

  return [...staticPages, ...entityPages, ...signalPages];
}
