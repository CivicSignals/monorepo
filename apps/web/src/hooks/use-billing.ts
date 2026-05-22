// Billing hooks (N4 + N5) — TanStack Query owns the server interactions (doc 06 §2).
//
// useBillingLimits: fetches GET /billing/limits and returns per-dimension
// enforcement states (ok | warning | exceeded) for the active workspace.
// The hook is used by the LimitBanner component to render the soft-limit
// banner and paywall CTA.
//
// N5 additions:
// useWorkspacePlan: fetches GET /billing/plan — effective plan + limits + features.
// useWorkspaceUsage: fetches GET /billing/usage — current-period usage vs limits.
// useChangePlan: mutation for POST /billing/change-plan (upgrade/downgrade).
// useCreatePortalSession: mutation for POST /billing/portal-session.

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type WorkspaceLimits,
  type WorkspacePlanInfo,
  type WorkspaceUsage,
  type SubscriptionPlan,
  type LimitState,
  getWorkspaceLimits,
  getWorkspacePlan,
  getWorkspaceUsage,
  changePlan,
  createPortalSession,
} from "@/lib/billing-api";
import { useSessionStore } from "@/store/session";

export {
  type LimitState,
  type WorkspaceLimits,
  type WorkspacePlanInfo,
  type WorkspaceUsage,
  type SubscriptionPlan,
};

export const BILLING_LIMITS_QUERY_KEY = (workspaceId: string) =>
  ["billing", "limits", workspaceId] as const;

/**
 * Fetch per-dimension limit states for the given workspace.
 *
 * Returns `undefined` when the user is not authenticated or no workspaceId
 * is provided. Polls every 5 minutes so the banner updates during long sessions.
 *
 * N4: drives the soft-limit banner and paywall CTA in the app shell.
 */
export function useBillingLimits(workspaceId: string | null | undefined) {
  const token = useSessionStore((s) => s.accessToken);

  return useQuery<WorkspaceLimits | null>({
    queryKey: BILLING_LIMITS_QUERY_KEY(workspaceId ?? ""),
    queryFn: () => {
      if (!token || !workspaceId) return Promise.resolve(null);
      return getWorkspaceLimits(token, workspaceId);
    },
    enabled: token !== null && !!workspaceId,
    staleTime: 5 * 60_000, // 5 minutes
    refetchInterval: 5 * 60_000,
  });
}

/**
 * Derive the worst limit state across all dimensions.
 *
 * Returns 'exceeded' if any dimension is exceeded, 'warning' if any is at
 * warning, 'ok' otherwise (or when limits are not yet loaded).
 */
export function worstLimitState(limits: WorkspaceLimits | null | undefined): LimitState {
  if (!limits) return "ok";
  let worst: LimitState = "ok";
  for (const dim of Object.values(limits.dimensions)) {
    if (dim.state === "exceeded") return "exceeded";
    if (dim.state === "warning") worst = "warning";
  }
  return worst;
}

/**
 * Return all dimensions that are in `warning` or `exceeded` state.
 */
export function alertingDimensions(
  limits: WorkspaceLimits | null | undefined,
): Array<{ dimension: string; pct: number | null; state: LimitState }> {
  if (!limits) return [];
  return Object.values(limits.dimensions)
    .filter((d) => d.state !== "ok")
    .map((d) => ({ dimension: d.dimension, pct: d.pct, state: d.state }));
}

// ---------------------------------------------------------------------------
// N5: Plan info + usage queries + plan-change mutations
// ---------------------------------------------------------------------------

export const BILLING_PLAN_QUERY_KEY = (workspaceId: string) =>
  ["billing", "plan", workspaceId] as const;

export const BILLING_USAGE_QUERY_KEY = (workspaceId: string) =>
  ["billing", "usage", workspaceId] as const;

/**
 * Fetch the effective plan, limits, and feature flags for the workspace (N5).
 *
 * Returns null when not authenticated or no workspaceId provided.
 */
export function useWorkspacePlan(workspaceId: string | null | undefined) {
  const token = useSessionStore((s) => s.accessToken);

  return useQuery<WorkspacePlanInfo | null>({
    queryKey: BILLING_PLAN_QUERY_KEY(workspaceId ?? ""),
    queryFn: () => {
      if (!token || !workspaceId) return Promise.resolve(null);
      return getWorkspacePlan(token, workspaceId);
    },
    enabled: token !== null && !!workspaceId,
    staleTime: 2 * 60_000, // 2 minutes
  });
}

/**
 * Fetch current-period usage vs plan limits for the workspace (N5).
 *
 * Returns null when not authenticated or no workspaceId provided.
 */
export function useWorkspaceUsage(workspaceId: string | null | undefined) {
  const token = useSessionStore((s) => s.accessToken);

  return useQuery<WorkspaceUsage | null>({
    queryKey: BILLING_USAGE_QUERY_KEY(workspaceId ?? ""),
    queryFn: () => {
      if (!token || !workspaceId) return Promise.resolve(null);
      return getWorkspaceUsage(token, workspaceId);
    },
    enabled: token !== null && !!workspaceId,
    staleTime: 60_000, // 1 minute
  });
}

/**
 * Mutation: upgrade or downgrade the workspace plan (N5).
 *
 * On success, invalidates the plan, usage, and limits queries so the UI
 * reflects the new plan immediately.
 */
export function useChangePlan(workspaceId: string | null | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (targetPlan: SubscriptionPlan) => {
      if (!token || !workspaceId) {
        return Promise.reject(new Error("Not authenticated or no workspace"));
      }
      return changePlan(token, workspaceId, targetPlan);
    },
    onSuccess: () => {
      if (workspaceId) {
        void queryClient.invalidateQueries({
          queryKey: BILLING_PLAN_QUERY_KEY(workspaceId),
        });
        void queryClient.invalidateQueries({
          queryKey: BILLING_USAGE_QUERY_KEY(workspaceId),
        });
        void queryClient.invalidateQueries({
          queryKey: BILLING_LIMITS_QUERY_KEY(workspaceId),
        });
      }
    },
  });
}

/**
 * Mutation: create a Stripe Customer Portal session (N5).
 *
 * On success, the caller should redirect to the returned URL.
 */
export function useCreatePortalSession(workspaceId: string | null | undefined) {
  const token = useSessionStore((s) => s.accessToken);

  return useMutation({
    mutationFn: (returnUrl: string) => {
      if (!token || !workspaceId) {
        return Promise.reject(new Error("Not authenticated or no workspace"));
      }
      return createPortalSession(token, workspaceId, returnUrl);
    },
  });
}
