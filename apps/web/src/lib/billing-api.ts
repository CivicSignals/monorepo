// Billing API client for the web app (N4 + N5).
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

// ---------------------------------------------------------------------------
// Plan info (N2/N5)
// ---------------------------------------------------------------------------

/** Subscription plan values. */
export type SubscriptionPlan =
  | "self_hosted"
  | "solo"
  | "starter"
  | "pro"
  | "enterprise";

/** Subscription status values. */
export type SubscriptionStatus =
  | "trialing"
  | "active"
  | "past_due"
  | "read_only"
  | "suspended"
  | "cancelled";

/** Response from POST /billing/change-plan. */
export interface ChangePlanResult {
  workspace_id: string;
  plan: SubscriptionPlan;
  status: SubscriptionStatus;
  stripe_subscription_id: string | null;
  stripe_price_id: string | null;
}

/** Response from GET /billing/plan (WorkspacePlanOut). */
export interface PlanLimits {
  seats: number | null;
  tracked_entities: number | null;
  smart_searches_per_month: number | null;
  contact_exports_per_month: number | null;
  saved_searches: number | null;
  api_requests_per_month: number | null;
  ai_runs_per_month: number | null;
}

export interface WorkspacePlan {
  plan: SubscriptionPlan;
  display_name: string;
  features: string[];
  limits: PlanLimits;
}

export interface WorkspacePlanInfo {
  workspace_id: string;
  effective_plan: WorkspacePlan;
  all_features: string[];
}

/** Response from GET /billing/usage (WorkspaceUsageOut). */
export interface DimensionUsage {
  dimension: string;
  used: number;
  limit: number | null;
  pct_used: number | null;
}

export interface WorkspaceUsage {
  workspace_id: string;
  period: string;
  dimensions: Record<string, DimensionUsage>;
}

/**
 * GET /billing/plan
 *
 * Returns the effective plan, limits, and feature flags for the workspace.
 */
export function getWorkspacePlan(
  token: string,
  workspaceId: string,
): Promise<WorkspacePlanInfo> {
  return request<WorkspacePlanInfo>("/billing/plan", { token, workspaceId });
}

/**
 * GET /billing/usage
 *
 * Returns current-period usage vs plan limits for the workspace.
 */
export function getWorkspaceUsage(
  token: string,
  workspaceId: string,
): Promise<WorkspaceUsage> {
  return request<WorkspaceUsage>("/billing/usage", { token, workspaceId });
}

// ---------------------------------------------------------------------------
// N5: Self-serve plan change + portal session
// ---------------------------------------------------------------------------

/**
 * POST /billing/change-plan
 *
 * Upgrade or downgrade the workspace subscription to the target plan.
 * Stripe handles proration. Admin-gated.
 */
export function changePlan(
  token: string,
  workspaceId: string,
  targetPlan: SubscriptionPlan,
): Promise<ChangePlanResult> {
  return request<ChangePlanResult>("/billing/change-plan", {
    method: "POST",
    token,
    workspaceId,
    body: JSON.stringify({ target_plan: targetPlan }),
  });
}

/**
 * POST /billing/portal-session
 *
 * Create a Stripe Customer Portal session. Returns the portal URL.
 * Admin-gated.
 */
export function createPortalSession(
  token: string,
  workspaceId: string,
  returnUrl: string,
): Promise<{ url: string }> {
  return request<{ url: string }>("/billing/portal-session", {
    method: "POST",
    token,
    workspaceId,
    body: JSON.stringify({ return_url: returnUrl }),
  });
}
