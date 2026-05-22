// Billing API client for the web app (N4).
//
// Talks to the FastAPI billing endpoints under NEXT_PUBLIC_API_BASE_URL.
// TanStack Query owns the server state (see src/hooks/use-billing.ts);
// this module is the thin transport. Errors are RFC 7807 application/problem+json
// (doc 08 §1.7) and surfaced as ProblemError.

import { ProblemError, type Problem } from "@/lib/auth-api";

export type { Problem, ProblemError } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// ---------------------------------------------------------------------------
// Limit states (N4)
// ---------------------------------------------------------------------------

/** Enforcement state for a single metering dimension. */
export type LimitState = "ok" | "warning" | "exceeded";

/** Per-dimension limit info returned by GET /billing/limits. */
export interface DimensionLimit {
  dimension: string;
  used: number;
  limit: number | null; // null = unlimited
  pct: number | null; // null when unlimited
  state: LimitState;
}

/** Response from GET /billing/limits. */
export interface WorkspaceLimits {
  workspace_id: string;
  period: string; // YYYY-MM-DD (first day of billing month)
  dimensions: Record<string, DimensionLimit>;
}

// ---------------------------------------------------------------------------
// HTTP transport
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/**
 * GET /billing/limits
 *
 * Returns per-dimension limit states (ok | warning | exceeded) for the
 * current workspace. Drives the soft-limit banner (N4).
 */
export function getWorkspaceLimits(
  token: string,
  workspaceId: string,
): Promise<WorkspaceLimits> {
  return request<WorkspaceLimits>("/billing/limits", { token, workspaceId });
}
