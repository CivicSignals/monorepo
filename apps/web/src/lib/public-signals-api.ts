// Public signals API — server-side fetch helpers (P2).
//
// Used exclusively by Next.js server components / RSC in the public /s routes.
// These functions run on the server; they call the API directly with `fetch`
// and use Next.js caching semantics (revalidate / tags).
//
// Signals are global/public, like the entity directory: no auth header, no
// X-Workspace-Id (doc 07 §3 — a signal row is the same for everyone; the
// per-workspace *score* is the G1 feed, which is authenticated and lives
// elsewhere). These helpers power the public, indexable signal pages (P2) — the
// signal analogue of the C5 public entity directory.
//
// TODO P4: add rate-limit / politeness guards to these helpers + the sitemap walk.

import type { SignalRead, SignalType } from "@/lib/signals-api";

export type { SignalRead };

// ---- Public-surface allowlist (mirrors signals/schemas.py PUBLIC_SIGNAL_TYPES) ----
//
// Only late-stage public signal types are publicly indexable (doc 13 §4.1, §4.6):
// public RFPs, public news mentions, public grant awards. Everything else is
// paid-tier value upstream of the public layer and must not be exposed publicly.
// The server is authoritative (the /public + /sources endpoints 404 a non-public
// type); this client-side set keeps the sitemap walk from publishing /s/[id] URLs
// for non-public signals (which would 404 anyway).
export const PUBLIC_SIGNAL_TYPES: ReadonlySet<SignalType> = new Set<SignalType>([
  "rfp_posted",
  "news_mention",
  "grant_awarded",
]);

/** Whether a signal type slug is in the public allowlist (doc 13 §4.1, §4.6). */
export function isPublicSignalType(signalType: string): boolean {
  return PUBLIC_SIGNAL_TYPES.has(signalType as SignalType);
}

// ---- Public signal projection (mirrors signals/schemas.py PublicSignalRead) ----

/**
 * The public-safe projection of a signal returned by GET /signals/:id/public.
 * Narrower than the internal SignalRead — no content_hash / raw_document_ids /
 * confidence / status / review_required / is_degraded / details (doc 13 §4.2).
 */
export interface PublicSignalRead {
  id: string;
  signal_type: string;
  title: string;
  summary: string;
  entity_name: string | null;
  occurred_at: string | null;
  observed_at: string;
}

// ---- Source citation types (mirrors signals/schemas.py SignalSourcesRead) ----

/** One public-safe source citation for a signal (mirrors SignalSource). */
export interface PublicSignalSource {
  document_id: string;
  source_url: string;
  recipe_id: string;
  fetched_at: string | null;
}

/** A signal's resolved source citations (mirrors SignalSourcesRead). */
export interface PublicSignalSources {
  signal_id: string;
  sources: PublicSignalSource[];
}

// ---- Signal list page (mirrors signals/schemas.py SignalPage) ----

export interface PublicSignalPage {
  items: SignalRead[];
  next_cursor: string | null;
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// How long to cache signal data on the CDN / Next.js data cache (seconds).
// Public signal pages are relatively stable once published; revalidate every 5 min
// (matches the directory's DIRECTORY_REVALIDATE_SECONDS for a consistent cache TTL).
export const SIGNALS_REVALIDATE_SECONDS = 300;

// Maximum signals enumerated per sitemap generation (paginated; P4 rate-limit TODO).
export const SITEMAP_SIGNAL_LIMIT = 1000;

/**
 * Fetch the public projection of a signal for a public /s/[id] page.
 *
 * Hits GET /signals/:id/public — the narrowed, public-type-gated read shape (P2;
 * doc 13 §4.1, §4.6, §4.2), NOT the full internal SignalRead. The server 404s when
 * the signal does not exist or its type is not in the public allowlist, so this
 * returns null and the page renders not-found (a paid-tier signal is never exposed).
 */
export async function fetchPublicSignal(
  id: string,
): Promise<PublicSignalRead | null> {
  const url = `${API_BASE_URL}/signals/${id}/public`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: SIGNALS_REVALIDATE_SECONDS },
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`Failed to fetch signal ${id}: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as PublicSignalRead;
}

/**
 * Fetch a signal's source citations (P2 — prominent source attribution).
 * Public/global resource; no auth needed. Returns an empty list when the signal
 * has no resolvable documents. Non-fatal callers should catch and degrade so the
 * page still renders if this sub-fetch fails.
 */
export async function fetchPublicSignalSources(
  id: string,
): Promise<PublicSignalSources> {
  const url = `${API_BASE_URL}/signals/${id}/sources`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: SIGNALS_REVALIDATE_SECONDS },
  });
  if (!res.ok) {
    throw new Error(
      `Failed to fetch sources for signal ${id}: ${res.status} ${res.statusText}`,
    );
  }
  return (await res.json()) as PublicSignalSources;
}

/** Fetch a page of global signals for the public index / sitemap walk. */
export async function fetchPublicSignals(
  opts: { cursor?: string; limit?: number; signal_type?: string } = {},
): Promise<PublicSignalPage> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 25));
  if (opts.cursor) params.set("cursor", opts.cursor);
  if (opts.signal_type) params.set("signal_type", opts.signal_type);

  const url = `${API_BASE_URL}/signals?${params.toString()}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: SIGNALS_REVALIDATE_SECONDS },
  });
  if (!res.ok) {
    throw new Error(`Failed to fetch signals: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as PublicSignalPage;
}

/**
 * Collect up to SITEMAP_SIGNAL_LIMIT public-type signal IDs for sitemap generation.
 * Paginates through the API until exhausted or limit reached (mirrors the entity
 * directory's fetchAllEntityIdsForSitemap, C5).
 *
 * Only signals whose type is in PUBLIC_SIGNAL_TYPES (doc 13 §4.1, §4.6) are emitted —
 * a non-public (paid-tier) signal must not appear in the public sitemap (and its
 * /s/[id] page 404s server-side anyway). The page filters by type rather than
 * issuing one request per public type so the walk stays a single keyset cursor pass.
 *
 * TODO P4: add rate-limit / politeness between pages in high-load prod.
 */
export async function fetchAllSignalIdsForSitemap(): Promise<string[]> {
  const ids: string[] = [];
  let cursor: string | null = null;
  const pageSize = 100;

  while (ids.length < SITEMAP_SIGNAL_LIMIT) {
    const page = await fetchPublicSignals({
      cursor: cursor ?? undefined,
      limit: pageSize,
    });
    for (const s of page.items) {
      if (ids.length >= SITEMAP_SIGNAL_LIMIT) break;
      if (isPublicSignalType(s.signal_type)) {
        ids.push(s.id);
      }
    }
    if (!page.next_cursor) break;
    cursor = page.next_cursor;
  }

  return ids;
}
