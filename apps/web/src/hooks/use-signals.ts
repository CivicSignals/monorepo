// Signal feed hooks (G1) — TanStack Query owns server interactions (doc 06 §2).
//
// The feed is workspace-scoped; query keys include workspaceId so cached data
// never bleeds across workspace switches (doc 08 §1.4, B5).
//
// useWorkspaceFeed   — paginated, filterable workspace feed (score desc)
// useLoadMoreFeed    — cursor "load more" by appending pages
// useSignalDetail    — one signal's full detail view (G2)

"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import {
  type FeedFilters,
  type FeedPage,
  type SignalDetailRead,
  FEED_PAGE_LIMIT,
  getSignalDetail,
  listFeedSignals,
} from "@/lib/signals-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

// ---- Auth/workspace helpers ------------------------------------------------

function useAuth() {
  const token = useSessionStore((s) => s.accessToken);
  const workspaceId = useUiStore((s) => s.activeWorkspaceId);
  return { token, workspaceId };
}

// ---- Query keys (workspace-scoped to prevent cross-tenant cache bleed) ------

export const feedKeys = {
  all: ["signals-feed"] as const,
  workspace: (workspaceId: string | null) =>
    [...feedKeys.all, workspaceId ?? "none"] as const,
  list: (workspaceId: string | null, filters: Omit<FeedFilters, "cursor">) =>
    [...feedKeys.workspace(workspaceId), "list", filters] as const,
  detail: (workspaceId: string | null, signalId: string) =>
    [...feedKeys.workspace(workspaceId), "detail", signalId] as const,
};

// ---- useWorkspaceFeed -------------------------------------------------------

/**
 * Infinite-query hook for the workspace signal feed (G1).
 *
 * Returns an infinite list of feed pages sorted by score desc. Each "load more"
 * appends the next cursor page (never offset). Filters are forwarded to the API;
 * changing a filter key resets the cursor and re-fetches from page 1.
 *
 * Server state only — do NOT put feed data in Zustand (doc 06 §2).
 *
 * TODO G5: polished loading/empty/error states (leave seams here).
 */
export function useWorkspaceFeed(filters: Omit<FeedFilters, "cursor"> = {}) {
  const { token, workspaceId } = useAuth();

  return useInfiniteQuery<FeedPage, Error>({
    queryKey: feedKeys.list(workspaceId, filters),
    queryFn: ({ pageParam }) => {
      if (!token || !workspaceId) {
        return Promise.resolve({
          data: [],
          page: { next_cursor: null, has_more: false, limit: FEED_PAGE_LIMIT },
        });
      }
      return listFeedSignals(token, workspaceId, {
        ...filters,
        cursor: (pageParam as string | undefined) ?? undefined,
        limit: FEED_PAGE_LIMIT,
      });
    },
    initialPageParam: undefined,
    getNextPageParam: (lastPage) =>
      lastPage.page.has_more ? lastPage.page.next_cursor : undefined,
    enabled: token !== null && workspaceId !== null,
  });
}

// ---- useSignalDetail --------------------------------------------------------

/**
 * Query hook for a single signal's full detail view (G2).
 *
 * Workspace-scoped: the query key includes workspaceId so the per-workspace
 * score/status never bleeds across workspace switches. Returns the global signal
 * plus source documents, suggested contacts, and related signals.
 *
 * Server state only — do NOT put detail data in Zustand (doc 06 §2).
 */
export function useSignalDetail(signalId: string | undefined) {
  const { token, workspaceId } = useAuth();

  return useQuery<SignalDetailRead, Error>({
    queryKey: feedKeys.detail(workspaceId, signalId ?? "none"),
    queryFn: () => {
      // Guarded by `enabled`; the non-null assertions are safe here.
      return getSignalDetail(token!, workspaceId!, signalId!);
    },
    enabled:
      token !== null &&
      workspaceId !== null &&
      signalId !== undefined &&
      signalId !== "",
  });
}
