// Public entities API — server-side fetch helpers (C5).
//
// Used exclusively by Next.js server components / RSC in the /directory routes.
// These functions run on the server; they call the API directly with `fetch`
// and use Next.js caching semantics (revalidate / tags).
//
// The entities API is public/global: no auth header, no X-Workspace-Id.
// Doc 07 §3, C1 req 5, C5 req 1.
//
// Sitemap cap: SITEMAP_ENTITY_LIMIT entities max (paginated in batches).

import type { EntityPage, EntityRead, EntityFilters } from "@/lib/entities-api";

export type { EntityRead, EntityPage };

// Re-export the same types for convenience in server components.

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// How long to cache entity data on the CDN / Next.js data cache (seconds).
// Public directory pages are relatively stable; revalidate every 5 minutes.
export const DIRECTORY_REVALIDATE_SECONDS = 300;

// Maximum entities fetched per sitemap generation (paginated, P4 rate-limit TODO).
export const SITEMAP_ENTITY_LIMIT = 1000;

/** Fetch a page of entities for the public directory. */
export async function fetchPublicEntities(
  filters: Omit<EntityFilters, "limit"> & { limit?: number },
): Promise<EntityPage> {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.type) {
    for (const t of filters.type) params.append("type", t);
  }
  if (filters.state) {
    for (const s of filters.state) params.append("state", s);
  }
  if (filters.status) params.set("status", filters.status);
  if (filters.cursor) params.set("cursor", filters.cursor);
  params.set("limit", String(filters.limit ?? 25));

  const url = `${API_BASE_URL}/entities${params.size > 0 ? `?${params.toString()}` : ""}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: DIRECTORY_REVALIDATE_SECONDS },
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch entities: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as EntityPage;
}

/** Fetch a single entity by id for a public profile page. Returns null on 404. */
export async function fetchPublicEntity(id: string): Promise<EntityRead | null> {
  const url = `${API_BASE_URL}/entities/${id}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: DIRECTORY_REVALIDATE_SECONDS },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`Failed to fetch entity ${id}: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as EntityRead;
}

/** Fetch children of an entity for the public profile page. */
export async function fetchPublicEntityChildren(
  id: string,
  opts: { cursor?: string; limit?: number } = {},
): Promise<EntityPage> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 25));
  if (opts.cursor) params.set("cursor", opts.cursor);

  const url = `${API_BASE_URL}/entities/${id}/children?${params.toString()}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: DIRECTORY_REVALIDATE_SECONDS },
  });
  if (!res.ok) {
    throw new Error(
      `Failed to fetch children of entity ${id}: ${res.status} ${res.statusText}`,
    );
  }
  return (await res.json()) as EntityPage;
}

/**
 * Collect up to SITEMAP_ENTITY_LIMIT entity IDs for sitemap generation.
 * Paginates through the API until exhausted or limit reached.
 *
 * TODO P4: add rate-limit / politeness between pages in high-load prod.
 */
export async function fetchAllEntityIdsForSitemap(): Promise<string[]> {
  const ids: string[] = [];
  let cursor: string | null = null;
  const pageSize = 100;

  while (ids.length < SITEMAP_ENTITY_LIMIT) {
    const remaining = SITEMAP_ENTITY_LIMIT - ids.length;
    const limit = Math.min(pageSize, remaining);
    const page = await fetchPublicEntities({ cursor: cursor ?? undefined, limit });
    for (const e of page.items) {
      ids.push(e.id);
    }
    if (!page.next_cursor) break;
    cursor = page.next_cursor;
  }

  return ids;
}
