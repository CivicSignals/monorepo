// ICP API client for the web app (F2).
//
// Talks to the FastAPI ICP endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1/icp). TanStack Query owns the *server*
// state (see src/hooks/use-icp.ts); this module is the thin transport layer.
//
// ICPs are workspace-scoped — every call sends the X-Workspace-Id header
// (doc 08 §1.4, B5). The ICP wizard (F2) drives these same endpoints.

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// ---- Enums (mirrors apps/api/src/civicsignals_api/modules/icp/schemas.py) ----

export type SignalType =
  | "rfp_posted"
  | "budget_drafted"
  | "personnel_change"
  | "grant_awarded"
  | "board_decision"
  | "news_mention";

export const SIGNAL_TYPES: SignalType[] = [
  "rfp_posted",
  "budget_drafted",
  "personnel_change",
  "grant_awarded",
  "board_decision",
  "news_mention",
];

export const SIGNAL_TYPE_LABELS: Record<SignalType, string> = {
  rfp_posted: "RFP Posted",
  budget_drafted: "Budget Drafted",
  personnel_change: "Personnel Change",
  grant_awarded: "Grant Awarded",
  board_decision: "Board Decision",
  news_mention: "News Mention",
};

export const SIGNAL_TYPE_DESCRIPTIONS: Record<SignalType, string> = {
  rfp_posted: "Requests for proposals your target entities have issued.",
  budget_drafted: "Draft or approved budget documents revealing spend plans.",
  personnel_change: "Leadership or procurement-role changes at target entities.",
  grant_awarded: "Federal / state grant awards flowing to target entities.",
  board_decision: "Key votes, resolutions, and agenda items from board meetings.",
  news_mention: "News articles and press releases mentioning target entities.",
};

export type EntityKind =
  | "k12_district"
  | "community_college"
  | "university"
  | "city"
  | "county"
  | "state_agency"
  | "special_district";

export const ENTITY_KINDS: EntityKind[] = [
  "k12_district",
  "community_college",
  "university",
  "city",
  "county",
  "state_agency",
  "special_district",
];

export const ENTITY_KIND_LABELS: Record<EntityKind, string> = {
  k12_district: "K-12 School District",
  community_college: "Community College",
  university: "University / 4-Year",
  city: "City / Municipality",
  county: "County",
  state_agency: "State Agency",
  special_district: "Special District",
};

// ---- Shapes (mirrors IcpCreate / IcpUpdate / IcpOut) ----

export interface IcpOut {
  id: string;
  workspace_id: string;
  name: string;
  countries: string[];
  states: string[];
  entity_kinds: EntityKind[];
  signal_types: SignalType[];
  min_size: number | null;
  max_size: number | null;
  signal_weights: Partial<Record<SignalType, number>>;
  keywords_required: string[];
  keywords_excluded: string[];
  deal_band_min_cents: number | null;
  deal_band_max_cents: number | null;
  threshold: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface IcpPage {
  items: IcpOut[];
  next_cursor: string | null;
}

export interface IcpCreate {
  name: string;
  countries?: string[];
  states?: string[];
  entity_kinds?: EntityKind[];
  signal_types?: SignalType[];
  min_size?: number | null;
  max_size?: number | null;
  signal_weights?: Partial<Record<SignalType, number>>;
  keywords_required?: string[];
  keywords_excluded?: string[];
  deal_band_min_cents?: number | null;
  deal_band_max_cents?: number | null;
  threshold?: number;
  is_active?: boolean;
}

export type IcpUpdate = Partial<IcpCreate>;

// ---- Transport ----

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
  // ICP is workspace-scoped (doc 08 §1.4, B5).
  if (workspaceId) merged.set("X-Workspace-Id", workspaceId);

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
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
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- Page size cap (doc 06 §5) ----

export const ICP_PAGE_LIMIT = 25;

// ---- ICP API functions (workspace-scoped) ----

/** List ICP definitions for the active workspace (cursor-paginated). */
export function listIcps(
  token: string,
  workspaceId: string,
  opts: { cursor?: string; limit?: number } = {},
): Promise<IcpPage> {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? ICP_PAGE_LIMIT));
  if (opts.cursor) params.set("cursor", opts.cursor);
  return request<IcpPage>(`/icp?${params.toString()}`, { token, workspaceId });
}

/** Get a single ICP definition by id. Returns null if 404. */
export async function getIcp(
  token: string,
  workspaceId: string,
  id: string,
): Promise<IcpOut | null> {
  try {
    return await request<IcpOut>(`/icp/${encodeURIComponent(id)}`, {
      token,
      workspaceId,
    });
  } catch (err) {
    if (err instanceof ProblemError && err.problem.status === 404) return null;
    throw err;
  }
}

/** Create a new ICP definition. */
export function createIcp(
  token: string,
  workspaceId: string,
  input: IcpCreate,
): Promise<IcpOut> {
  return request<IcpOut>("/icp", {
    method: "POST",
    body: JSON.stringify(input),
    token,
    workspaceId,
  });
}

/** Patch an ICP definition (partial update). */
export function updateIcp(
  token: string,
  workspaceId: string,
  id: string,
  update: IcpUpdate,
): Promise<IcpOut> {
  return request<IcpOut>(`/icp/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(update),
    token,
    workspaceId,
  });
}

/** Activate an ICP (deactivates any other active ICP in the workspace). */
export function activateIcp(
  token: string,
  workspaceId: string,
  id: string,
): Promise<IcpOut> {
  return request<IcpOut>(`/icp/${encodeURIComponent(id)}/activate`, {
    method: "POST",
    body: JSON.stringify({}),
    token,
    workspaceId,
  });
}

/** Deactivate an ICP (workspace will have no active ICP). */
export function deactivateIcp(
  token: string,
  workspaceId: string,
  id: string,
): Promise<IcpOut> {
  return request<IcpOut>(`/icp/${encodeURIComponent(id)}/deactivate`, {
    method: "POST",
    body: JSON.stringify({}),
    token,
    workspaceId,
  });
}
