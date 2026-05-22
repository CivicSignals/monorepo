// Saved-search API client for the web app (H1).
//
// Talks to the FastAPI searches endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1/searches). TanStack Query owns the *server*
// state (see src/hooks/use-searches.ts); this module is the thin transport layer.
//
// Saved searches are workspace-scoped — every call sends the X-Workspace-Id
// header (doc 08 §1.4, B5). A saved search captures the G1 feed filter set, so
// the filter shape is reused from signals-api (FeedFilters minus pagination).

import { ProblemError, type Problem } from "@/lib/auth-api";
import type { FeedStatus, SignalType } from "@/lib/signals-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// ---- Filter shape (the G1 feed filter subset a saved search captures) -------
//
// Mirrors searches.schemas.SearchFilters — the same params the feed endpoint /
// list_workspace_signals accepts, minus pagination (cursor/limit).

export interface SearchFilters {
  signal_type?: SignalType;
  statuses?: FeedStatus[];
  min_score?: number;
  published_at_gte?: string;
  published_at_lt?: string;
}

// ---- Shapes (mirrors SavedSearchCreate / SavedSearchUpdate / SavedSearchOut) -

export interface SavedSearchOut {
  id: string;
  workspace_id: string;
  created_by: string;
  name: string;
  filters: SearchFilters;
  is_shared: boolean;
  created_at: string;
  updated_at: string;
}

export interface SavedSearchPage {
  items: SavedSearchOut[];
  next_cursor: string | null;
}

export interface SavedSearchCreate {
  name: string;
  filters?: SearchFilters;
  is_shared?: boolean;
}

export interface SavedSearchUpdate {
  name?: string;
  filters?: SearchFilters;
  is_shared?: boolean;
}

// ---- Transport --------------------------------------------------------------

async function request<T>(
  path: string,
  init: RequestInit & { token?: string; workspaceId?: string } = {},
): Promise<T> {
  const { token, workspaceId, headers, ...rest } = init;
  const merged = new Headers(headers);
  merged.set("Accept", "application/json");
  if (rest.body !== undefined) {
    merged.set("Content-Type", "application/json");
  }
  if (token) merged.set("Authorization", `Bearer ${token}`);
  // Saved searches are workspace-scoped (doc 08 §1.4, B5).
  if (workspaceId) merged.set("X-Workspace-Id", workspaceId);

  const res = await fetch(`${API_BASE_URL}${path}`, { ...rest, headers: merged });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({
      type: "about:blank",
      title: res.statusText,
      status: res.status,
    }))) as Problem;
    throw new ProblemError(problem);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- Page size cap (doc 06 §5, doc 08 §1.5) --------------------------------

export const SAVED_SEARCH_PAGE_LIMIT = 25;

// ---- Saved-search API functions (workspace-scoped) -------------------------

/** List saved searches the caller can see (own + shared) in the workspace. */
export function listSavedSearches(
  token: string,
  workspaceId: string,
  opts: { cursor?: string; limit?: number } = {},
): Promise<SavedSearchPage> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? SAVED_SEARCH_PAGE_LIMIT));
  if (opts.cursor) params.set("cursor", opts.cursor);
  return request<SavedSearchPage>(`/searches?${params.toString()}`, {
    token,
    workspaceId,
  });
}

/** Create a new saved search owned by the caller. */
export function createSavedSearch(
  token: string,
  workspaceId: string,
  input: SavedSearchCreate,
): Promise<SavedSearchOut> {
  return request<SavedSearchOut>("/searches", {
    method: "POST",
    body: JSON.stringify(input),
    token,
    workspaceId,
  });
}

/** Patch a saved search (rename / re-filter / toggle sharing). Owner only. */
export function updateSavedSearch(
  token: string,
  workspaceId: string,
  id: string,
  update: SavedSearchUpdate,
): Promise<SavedSearchOut> {
  return request<SavedSearchOut>(`/searches/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(update),
    token,
    workspaceId,
  });
}

/** Delete a saved search. Owner only. */
export function deleteSavedSearch(
  token: string,
  workspaceId: string,
  id: string,
): Promise<void> {
  return request<void>(`/searches/${encodeURIComponent(id)}`, {
    method: "DELETE",
    token,
    workspaceId,
  });
}
