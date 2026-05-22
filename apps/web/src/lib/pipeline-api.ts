// Pipeline API client for the web app (J5).
//
// Talks to the FastAPI pipeline endpoints under NEXT_PUBLIC_API_BASE_URL.
// TanStack Query owns server state (see src/hooks/use-pipeline.ts); this module
// is the thin transport layer.
//
// All pipeline endpoints are workspace-scoped (X-Workspace-Id header, doc 08
// §1.4). The report endpoint (J5) is a lightweight read-only aggregation so it
// never needs a request body.

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// ---- Shapes (mirrors apps/api/src/civicsignals_api/modules/pipeline/schemas.py) ----

export interface StageRollup {
  stage_id: string;
  stage_name: string;
  stage_position: number;
  item_count: number;
  /** Sum of value_estimate for items in this stage; null when no values set. */
  total_value: string | null;
}

export interface PipelineReport {
  stages: StageRollup[];
  total_items: number;
  /** Workspace-level sum; null when no items have a value estimate. */
  total_value: string | null;
}

/** A single pipeline stage returned by the API (J1). */
export interface PipelineStage {
  id: string;
  workspace_id: string;
  name: string;
  position: number;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface PipelineStagePage {
  items: PipelineStage[];
  next_cursor: string | null;
}

/** A single pipeline item returned by the API (J1, J4). */
export interface PipelineItem {
  id: string;
  workspace_id: string;
  stage_id: string;
  /** null for manually-created items (J4); populated for signal-linked items. */
  signal_id: string | null;
  owner_id: string | null;
  title: string;
  notes: string | null;
  value_estimate: string | null;
  status: "active" | "won" | "lost" | "disqualified" | "archived";
  created_at: string;
  updated_at: string;
}

/** Request body for POST /pipeline/items/manual (J4). */
export interface ManualPipelineItemCreate {
  title: string;
  stage_id?: string;
  notes?: string;
  value_estimate?: string;
  owner_id?: string;
}

export interface PipelineItemPage {
  items: PipelineItem[];
  next_cursor: string | null;
}

/** Request body for POST /pipeline/items/{id}/move (J2). */
export interface MoveItemInput {
  stage_id: string;
  /** Optionally update status when moving (e.g. → "won" on a Won stage). */
  status?: PipelineItem["status"];
}

// ---- Transport ----

async function request<T>(
  path: string,
  init: RequestInit & { token?: string; workspaceId?: string } = {},
): Promise<T> {
  const { token, workspaceId, headers, ...rest } = init;
  const merged = new Headers(headers);
  merged.set("Accept", "application/json");
  if (token) merged.set("Authorization", `Bearer ${token}`);
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
  return (await res.json()) as T;
}

// ---- API functions ----

/** Fetch the workspace-level pipeline rollup report (J5). */
export function getPipelineReport(
  token: string,
  workspaceId: string,
): Promise<PipelineReport> {
  return request<PipelineReport>("/pipeline/report", { token, workspaceId });
}

/**
 * List the workspace's pipeline stages, ordered by position (J1, used by J2).
 * The board has a small, fixed set of stages (default nine), so this fetches a
 * generous page; cursor pagination is supported by the API but the Kanban board
 * does not paginate columns.
 */
export function listPipelineStages(
  token: string,
  workspaceId: string,
): Promise<PipelineStagePage> {
  return request<PipelineStagePage>("/pipeline/stages?limit=100", {
    token,
    workspaceId,
  });
}

/**
 * List pipeline items (J1, used by J2). Optionally filter by stage. The board
 * fetches a single page per workspace; for very large pipelines this should be
 * extended to follow next_cursor — see the Kanban board hook for the TODO.
 */
export function listPipelineItems(
  token: string,
  workspaceId: string,
  opts: { stageId?: string; cursor?: string; limit?: number } = {},
): Promise<PipelineItemPage> {
  const params = new URLSearchParams();
  if (opts.stageId) params.set("stage_id", opts.stageId);
  if (opts.cursor) params.set("cursor", opts.cursor);
  params.set("limit", String(opts.limit ?? 100));
  return request<PipelineItemPage>(`/pipeline/items?${params.toString()}`, {
    token,
    workspaceId,
  });
}

/**
 * Move a pipeline item to a different stage (J2 Kanban DnD).
 * Maps to POST /pipeline/items/{id}/move. Returns the updated item; a 404
 * (item/stage gone) or 409 (conflict) surfaces as a ProblemError the caller
 * inspects via `.problem.status` to drive conflict reconciliation.
 */
export function movePipelineItem(
  token: string,
  workspaceId: string,
  itemId: string,
  body: MoveItemInput,
): Promise<PipelineItem> {
  return request<PipelineItem>(`/pipeline/items/${itemId}/move`, {
    method: "POST",
    token,
    workspaceId,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/**
 * Create a manual pipeline item — not tied to a signal (J4).
 * Maps to POST /pipeline/items/manual.
 */
export function createManualPipelineItem(
  token: string,
  workspaceId: string,
  body: ManualPipelineItemCreate,
): Promise<PipelineItem> {
  return request<PipelineItem>("/pipeline/items/manual", {
    method: "POST",
    token,
    workspaceId,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
