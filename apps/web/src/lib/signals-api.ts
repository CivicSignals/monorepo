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

// ---- Score breakdown shape (F4; mirrors workspace_scoring.score_signal_against_icp) --
//
// The per-workspace score_breakdown JSONB persisted on WorkspaceScore (F3). Every
// field is optional on the client because the breakdown is a free-form JSONB blob —
// older rows / non-pipeline inserts may carry a partial (or empty) map, so the F4
// "Why this signal?" panel must degrade gracefully rather than assume the full shape.

/** One scorer component's contribution toward the 0..100 blended score. */
export interface ScoreComponent {
  /** Raw component value in [0, 1] before weighting. */
  value?: number;
  /** Normalised weight in [0, 1] (renormalised when semantic is absent). */
  weight?: number;
  /** Points this component contributed toward the 0..100 total. */
  points?: number;
}

/** Which ICP dimensions a signal satisfied (the matched flags). */
export interface ScoreMatched {
  signal_type?: boolean;
  country?: boolean;
  state?: boolean;
  entity_kind?: boolean;
  size_band?: boolean;
  states?: string[];
  keywords?: string[];
}

export interface ScoreBreakdown {
  components?: Record<string, ScoreComponent>;
  matched?: ScoreMatched;
  /** Pre-formatted human-readable bullets seeded by the scorer (doc 14 §6.2). */
  bullets?: string[];
}

/** Display labels for the scorer components (mirrors workspace_scoring weights). */
export const SCORE_COMPONENT_LABELS: Record<string, string> = {
  signal_type_weight: "Signal type",
  dimensions: "ICP dimensions",
  recency: "Recency",
  confidence: "Extraction confidence",
  keywords: "Keyword match",
  semantic: "Semantic similarity",
};

// ---- Signal detail shapes (G2; mirrors signals.schemas.SignalDetailRead) ----

export interface SourceDocumentRead {
  raw_document_id: string;
  recipe_id: string | null;
  source_url: string | null;
  fetched_at: string | null;
  content_type: string | null;
  missing: boolean;
}

export interface SuggestedContactRead {
  contact_id: string;
  name: string;
  title: string | null;
  department: string | null;
  canonical_email: string | null;
  status: string;
  verified: boolean;
}

export interface RelatedSignalRead {
  signal: SignalRead;
}

export interface SignalDetailRead {
  signal: SignalRead;
  entity_id: string | null;
  entity_name: string | null;
  score: number | null;
  status: FeedStatus | null;
  score_breakdown: Record<string, unknown> | null;
  matched_keywords: string[];
  extracted_fields: Record<string, unknown>;
  source_documents: SourceDocumentRead[];
  suggested_contacts: SuggestedContactRead[];
  related_signals: RelatedSignalRead[];
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

/**
 * Fetch one signal's full detail view in the workspace's context (G2).
 *
 * Returns the global signal + the calling workspace's score / breakdown (null
 * when the signal did not score into this workspace's feed — the corpus is
 * global), source documents, suggested contacts, and related signals.
 * Workspace-scoped via X-Workspace-Id (doc 08 §1.4).
 */
export function getSignalDetail(
  token: string,
  workspaceId: string,
  signalId: string,
): Promise<SignalDetailRead> {
  return request<SignalDetailRead>(
    `/signals/${encodeURIComponent(signalId)}/detail`,
    { token, workspaceId },
  );
}

// ---- Status transitions (G4) ------------------------------------------------

/**
 * The statuses the triage UI can request (G4). ``pushed`` is excluded — it is set
 * by the K-epic CRM-push flow, never by this human-driven control (doc 14 §5.3).
 */
export type SettableStatus = "new" | "reviewed" | "pinned" | "dismissed";

/** Response of the G4 PATCH: the transitioned score row's identity + new status. */
export interface StatusChangeRead {
  score_id: string;
  signal_id: string;
  status: FeedStatus;
}

/**
 * Transition a signal's per-workspace status (G4).
 *
 * PATCHes ``signals_workspace_score.status`` for the (workspace, signal) pair via
 * the validated transition graph. Workspace-scoped via X-Workspace-Id (doc 08 §1.4);
 * member-gated server-side (B7). A 404 means the signal did not score into this
 * workspace's feed; a 422 means the transition is illegal from the current status —
 * both surface as a {@link ProblemError}.
 */
export function changeSignalStatus(
  token: string,
  workspaceId: string,
  signalId: string,
  status: SettableStatus,
): Promise<StatusChangeRead> {
  return request<StatusChangeRead>(
    `/signals/${encodeURIComponent(signalId)}/status`,
    {
      method: "PATCH",
      token,
      workspaceId,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status }),
    },
  );
}
