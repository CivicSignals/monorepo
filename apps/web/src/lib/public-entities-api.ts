// Public entities API — server-side fetch helpers (C5, P1).
//
// Used exclusively by Next.js server components / RSC in the /directory routes.
// These functions run on the server; they call the API directly with `fetch`
// and use Next.js caching semantics (revalidate / tags).
//
// The entities API is public/global: no auth header, no X-Workspace-Id.
// Doc 07 §3, C1 req 5, C5 req 1.
//
// P1 extends this with public contacts and signals fetch helpers.
// Sitemap cap: SITEMAP_ENTITY_LIMIT entities max (paginated in batches).

import type { EntityPage, EntityRead, EntityFilters } from "@/lib/entities-api";

export type { EntityRead, EntityPage };

// ---- P1: Contact types (mirrors contacts/schemas.py ContactRead) ----

export interface PublicContactRead {
  id: string;
  entity_id: string;
  name: string;
  department: string | null;
  title: string | null;
  status: string;
  // canonical_email is intentionally omitted from the public display
  // (C2 req: don't expose raw emails without auth; show only verified + sourced info).
  source: string | null;
  source_url: string | null;
  confidence: number | null;
  verified: boolean;
  created_at: string;
  updated_at: string;
}

export interface PublicContactPage {
  items: PublicContactRead[];
  next_cursor: string | null;
}

// ---- P1: Signal types (mirrors signals/schemas.py SignalRead, public subset) ----

export interface PublicSignalRead {
  id: string;
  entity_id: string | null;
  entity_name_raw: string | null;
  signal_type: string;
  title: string;
  summary: string;
  occurred_at: string | null;
  observed_at: string;
  confidence: number | null;
  status: string;
  created_at: string;
}

export interface PublicSignalPage {
  items: PublicSignalRead[];
  next_cursor: string | null;
}

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

// ---- P1: public contacts + signals helpers ----

/**
 * Fetch public contacts for an entity (P1 req 2 — public contact subset).
 * Contacts are a global/public resource; no auth needed.
 * Returns first page only (limit=5) for the profile teaser.
 * Non-fatal: returns empty page on error so entity profile still renders.
 */
export async function fetchPublicEntityContacts(
  entityId: string,
  opts: { limit?: number } = {},
): Promise<PublicContactPage> {
  const params = new URLSearchParams();
  params.set("entity_id", entityId);
  params.set("limit", String(opts.limit ?? 5));

  const url = `${API_BASE_URL}/contacts?${params.toString()}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: DIRECTORY_REVALIDATE_SECONDS },
  });
  if (!res.ok) {
    throw new Error(
      `Failed to fetch contacts for entity ${entityId}: ${res.status} ${res.statusText}`,
    );
  }
  return (await res.json()) as PublicContactPage;
}

/**
 * Fetch recent public signals for an entity (P1 req 2 — recent-signal teaser).
 * Signals are a global/public resource; no auth needed.
 * Returns first page only (limit=3) for the profile teaser.
 * TODO P2: link to full public signal pages when G1+P2 are implemented.
 */
export async function fetchPublicEntitySignals(
  entityId: string,
  opts: { limit?: number } = {},
): Promise<PublicSignalPage> {
  const params = new URLSearchParams();
  params.set("entity_id", entityId);
  params.set("limit", String(opts.limit ?? 3));

  const url = `${API_BASE_URL}/signals?${params.toString()}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: DIRECTORY_REVALIDATE_SECONDS },
  });
  if (!res.ok) {
    throw new Error(
      `Failed to fetch signals for entity ${entityId}: ${res.status} ${res.statusText}`,
    );
  }
  return (await res.json()) as PublicSignalPage;
}
