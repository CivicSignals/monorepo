// Signals feed API client for the web app (G1).
//
// Talks to the FastAPI signals/feed endpoint under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1/signals/feed). TanStack Query owns the
// *server* state (see src/hooks/use-signals.ts); this module is the thin
// transport layer.
//
// The feed is workspace-scoped — every call sends the X-Workspace-Id header
// (doc 08 §1.4, B5). Sorted by score desc, cursor-paginated (doc 06 §5, doc 08 §1.5).

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// ---- Signal types (mirrors signals.schemas.SignalType) ----------------------

export type SignalType =
  | "rfp_posted"
  | "rfi_rfq"
  | "contract_expiring"
  | "contract_awarded"
  | "budget_approved"
  | "grant_awarded"
  | "grant_opportunity"
  | "leadership_change"
  | "board_agenda_item"
  | "strategic_plan_published"
  | "open_job"
  | "news_mention";

export const SIGNAL_TYPE_LABELS: Record<SignalType, string> = {
  rfp_posted: "RFP Posted",
  rfi_rfq: "RFI / RFQ",
  contract_expiring: "Contract Expiring",
  contract_awarded: "Contract Awarded",
  budget_approved: "Budget Approved",
  grant_awarded: "Grant Awarded",
  grant_opportunity: "Grant Opportunity",
  leadership_change: "Leadership Change",
  board_agenda_item: "Board Agenda Item",
  strategic_plan_published: "Strategic Plan Published",
  open_job: "Open Job",
  news_mention: "News Mention",
};

// ---- Feed status (mirrors workspace_score_model.SCORE_STATUSES) -------------

export type FeedStatus =
  | "new"
  | "reviewed"
  | "pinned"
  | "pushed"
  | "dismissed";

export const FEED_STATUS_LABELS: Record<FeedStatus, string> = {
  new: "New",
  reviewed: "Reviewed",
  pinned: "Pinned",
  pushed: "Pushed",
  dismissed: "Dismissed",
};

// ---- Response shapes (mirrors G1 FeedItemRead / FeedPage) ------------------

export interface SignalRead {
  id: string;
  entity_id: string | null;
  entity_name_raw: string | null;
  signal_type: string;
  recipe_id: string;
  raw_document_ids: string[];
  content_hash: string;
  occurred_at: string | null;
  observed_at: string;
  summary: string;
  title: string;
  details: Record<string, unknown>;
  confidence: number | null;
  status: string;
  is_degraded: boolean;
  review_required: boolean;
  created_at: string;
}

export interface FeedItemRead {
  score_id: string;
  signal: SignalRead;
  score: number;
  status: FeedStatus;
  score_breakdown: Record<string, unknown>;
  matched_keywords: string[];
  created_at: string;
}

export interface FeedPage {
  data: FeedItemRead[];
  page: {
    next_cursor: string | null;
    has_more: boolean;
    limit: number;
  };
}

// ---- Filter params (doc 08 §1.6) -------------------------------------------

export interface FeedFilters {
  signal_type?: SignalType;
  status?: FeedStatus[];
  min_score?: number;
  published_at_gte?: string;
  published_at_lt?: string;
  cursor?: string;
  limit?: number;
}

// ---- Transport --------------------------------------------------------------

async function request<T>(
  path: string,
  init: RequestInit & { token?: string; workspaceId?: string } = {},
): Promise<T> {
  const { token, workspaceId, headers, ...rest } = init;
  const merged = new Headers(headers);
  merged.set("Accept", "application/json");
  if (token) merged.set("Authorization", `Bearer ${token}`);
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

export const FEED_PAGE_LIMIT = 25;

// ---- Feed API functions (workspace-scoped) ----------------------------------

/**
 * Fetch one page of the workspace feed, sorted by score desc (G1).
 * Workspace-scoped via X-Workspace-Id (doc 08 §1.4).
 */
export function listFeedSignals(
  token: string,
  workspaceId: string,
  filters: FeedFilters = {},
): Promise<FeedPage> {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? FEED_PAGE_LIMIT));
  if (filters.signal_type) params.set("signal_type", filters.signal_type);
  if (filters.status) {
    for (const s of filters.status) params.append("status", s);
  }
  if (filters.min_score !== undefined)
    params.set("min_score", String(filters.min_score));
  if (filters.published_at_gte)
    params.set("published_at_gte", filters.published_at_gte);
  if (filters.published_at_lt)
    params.set("published_at_lt", filters.published_at_lt);
  if (filters.cursor) params.set("cursor", filters.cursor);
  return request<FeedPage>(`/signals/feed?${params.toString()}`, {
    token,
    workspaceId,
  });
}
