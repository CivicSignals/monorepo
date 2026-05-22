// Entities API client for the web app (C3).
//
// Talks to the FastAPI entity endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1). TanStack Query owns the *server* state
// (see src/hooks/use-entities.ts); this module is the thin transport layer.
//
// The entity directory is **public / global** — no X-Workspace-Id header is
// needed (doc 07 §3, C1 req 5). Query params mirror the API exactly:
// q, type, kind, state, geo_id, status, parent_id, cursor, limit.

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// ---- Shapes (mirrors apps/api/src/civicsignals_api/modules/entities/schemas.py) ----

export interface EntityRead {
  id: string;
  type: string;
  status: string;
  name: string;
  short_name: string | null;
  country: string;
  state: string | null;
  region: string | null;
  kind_id: string | null;
  geo_id: string | null;
  parent_id: string | null;
  nces_leaid: string | null;
  ipeds_unitid: string | null;
  census_gid: string | null;
  population: number | null;
  enrollment: number | null;
  annual_budget_usd: number | null;
  primary_website: string | null;
  procurement_portal_url: string | null;
  board_meeting_cadence: string | null;
  attributes: Record<string, unknown>;
  source_urls: string[];
  created_at: string;
  updated_at: string;
}

export interface EntityPage {
  items: EntityRead[];
  next_cursor: string | null;
}

export interface EntityFilters {
  q?: string;
  type?: string[];
  kind?: string[];
  state?: string[];
  geo_id?: string;
  status?: string;
  parent_id?: string;
  cursor?: string;
  limit?: number;
}

// ---- Transport ----

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const merged = new Headers(init.headers);
  merged.set("Accept", "application/json");

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: merged,
  });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({
      type: "about:blank",
      title: res.statusText,
      status: res.status,
    }))) as Problem;
    throw new ProblemError(problem);
  }
  return (await res.json()) as T;
}

// ---- API functions ----

export const ENTITIES_PAGE_LIMIT = 25;

/** List/search entities with optional filters and cursor pagination. */
export function listEntities(filters: EntityFilters = {}): Promise<EntityPage> {
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.type) {
    for (const t of filters.type) params.append("type", t);
  }
  if (filters.kind) {
    for (const k of filters.kind) params.append("kind", k);
  }
  if (filters.state) {
    for (const s of filters.state) params.append("state", s);
  }
  if (filters.geo_id) params.set("geo_id", filters.geo_id);
  if (filters.status) params.set("status", filters.status);
  if (filters.parent_id) params.set("parent_id", filters.parent_id);
  if (filters.cursor) params.set("cursor", filters.cursor);
  params.set("limit", String(filters.limit ?? ENTITIES_PAGE_LIMIT));

  const qs = params.toString();
  return request<EntityPage>(`/entities${qs ? `?${qs}` : ""}`);
}

/** Get a single entity by id. Returns null if 404. */
export async function getEntity(id: string): Promise<EntityRead | null> {
  try {
    return await request<EntityRead>(`/entities/${id}`);
  } catch (err) {
    if (err instanceof ProblemError && err.problem.status === 404) return null;
    throw err;
  }
}

/** List direct children of an entity with cursor pagination. */
export function listEntityChildren(
  id: string,
  opts: { cursor?: string; limit?: number } = {},
): Promise<EntityPage> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? ENTITIES_PAGE_LIMIT));
  if (opts.cursor) params.set("cursor", opts.cursor);
  return request<EntityPage>(`/entities/${id}/children?${params.toString()}`);
}
