// Billing hooks (N4) — TanStack Query owns the server interactions (doc 06 §2).
//
// useBillingLimits: fetches GET /billing/limits and returns per-dimension
// enforcement states (ok | warning | exceeded) for the active workspace.
// The hook is used by the LimitBanner component to render the soft-limit
// banner and paywall CTA.

"use client";

import { useQuery } from "@tanstack/react-query";
import {
  type WorkspaceLimits,
  type LimitState,
  getWorkspaceLimits,
} from "@/lib/billing-api";
import { useSessionStore } from "@/store/session";

export { type LimitState, type WorkspaceLimits };

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
