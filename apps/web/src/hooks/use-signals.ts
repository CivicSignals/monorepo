// Signal feed hooks (G1) — TanStack Query owns server interactions (doc 06 §2).
//
// The feed is workspace-scoped; query keys include workspaceId so cached data
// never bleeds across workspace switches (doc 08 §1.4, B5).
//
// useWorkspaceFeed   — paginated, filterable workspace feed (score desc)
// useLoadMoreFeed    — cursor "load more" by appending pages
// useSignalDetail    — one signal's full detail view (G2)

"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import {
  type BulkStatusChangeRead,
  type FeedbackKind,
  type FeedbackRead,
  type FeedFilters,
  type FeedItemRead,
  type FeedPage,
  type SettableStatus,
  type SignalDetailRead,
  type StatusChangeRead,
  FEED_PAGE_LIMIT,
  changeSignalStatus,
  changeSignalStatusBulk,
  getSignalDetail,
  listFeedSignals,
  retractSignalFeedback,
  submitSignalFeedback,
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

// ---- useChangeSignalStatus --------------------------------------------------

/** Variables for the G4 status mutation: which signal, and the target status. */
export interface ChangeStatusVars {
  signalId: string;
  status: SettableStatus;
}

/** Snapshot of cache entries we touched, kept so onError can roll the UI back. */
interface StatusRollback {
  feed: Array<[readonly unknown[], InfiniteData<FeedPage> | undefined]>;
  detail: SignalDetailRead | undefined;
}

/** Apply ``status`` to every matching feed item across all cached feed pages. */
function patchFeedStatus(
  data: InfiniteData<FeedPage> | undefined,
  signalId: string,
  status: SettableStatus,
): InfiniteData<FeedPage> | undefined {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      data: page.data.map((item: FeedItemRead) =>
        item.signal.id === signalId ? { ...item, status } : item,
      ),
    })),
  };
}

/**
 * Mutation hook for the G4 per-signal status transition (mark reviewed / pin /
 * dismiss / restore). Optimistically updates the signal's status in the feed list
 * caches *and* its detail cache, rolls back on error, and invalidates the feed +
 * detail queries on settle so the server stays the source of truth (doc 06 §2).
 *
 * Server state only — the optimistic write lives in the TanStack Query cache, never
 * Zustand. The PATCH is workspace-scoped + member-gated server-side (B7).
 */
export function useChangeSignalStatus() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<StatusChangeRead, Error, ChangeStatusVars, StatusRollback>({
    mutationFn: ({ signalId, status }) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return changeSignalStatus(token, workspaceId, signalId, status);
    },
    onMutate: async ({ signalId, status }) => {
      // Cancel in-flight feed/detail fetches so they can't clobber our optimistic write.
      await queryClient.cancelQueries({
        queryKey: feedKeys.workspace(workspaceId),
      });

      // Snapshot every feed-list cache for this workspace + the detail cache.
      const feed = queryClient.getQueriesData<InfiniteData<FeedPage>>({
        queryKey: [...feedKeys.workspace(workspaceId), "list"],
      });
      const detailKey = feedKeys.detail(workspaceId, signalId);
      const detail = queryClient.getQueryData<SignalDetailRead>(detailKey);

      // Optimistically patch the feed lists.
      for (const [key] of feed) {
        queryClient.setQueryData<InfiniteData<FeedPage>>(key, (old) =>
          patchFeedStatus(old, signalId, status),
        );
      }
      // Optimistically patch the detail cache.
      if (detail) {
        queryClient.setQueryData<SignalDetailRead>(detailKey, {
          ...detail,
          status,
        });
      }

      return { feed, detail };
    },
    onError: (_err, { signalId }, context) => {
      // Roll the optimistic writes back to the snapshots.
      if (!context) return;
      for (const [key, snapshot] of context.feed) {
        queryClient.setQueryData(key, snapshot);
      }
      if (context.detail) {
        queryClient.setQueryData(
          feedKeys.detail(workspaceId, signalId),
          context.detail,
        );
      }
    },
    onSettled: (_data, _err, { signalId }) => {
      // Re-sync from the server: the feed ordering / visibility may have shifted
      // (e.g. a dismissed row drops out of the default visible set).
      void queryClient.invalidateQueries({
        queryKey: feedKeys.workspace(workspaceId),
      });
      void queryClient.invalidateQueries({
        queryKey: feedKeys.detail(workspaceId, signalId),
      });
    },
  });
}

// ---- useBulkChangeSignalStatus (G3) -----------------------------------------

/** Variables for the G3 bulk status mutation: which signals, and the target status. */
export interface BulkChangeStatusVars {
  signalIds: string[];
  status: SettableStatus;
}

/** Snapshot of the feed caches we touched, kept so onError can roll the UI back. */
interface BulkStatusRollback {
  feed: Array<[readonly unknown[], InfiniteData<FeedPage> | undefined]>;
}

/** Apply ``status`` to every feed item whose signal id is in ``ids`` across all pages. */
function patchFeedStatusMany(
  data: InfiniteData<FeedPage> | undefined,
  ids: Set<string>,
  status: SettableStatus,
): InfiniteData<FeedPage> | undefined {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      data: page.data.map((item: FeedItemRead) =>
        ids.has(item.signal.id) ? { ...item, status } : item,
      ),
    })),
  };
}

/**
 * Mutation hook for the G3 bulk status transition (mass dismiss / mass pin).
 *
 * Optimistically applies ``status`` to every selected signal across the cached feed
 * lists, rolls back on error, and invalidates the workspace's feed queries on settle so
 * the server (the source of truth for which rows actually transitioned, and the
 * resulting feed ordering / visibility) wins. The clearing of the selection itself is
 * the caller's responsibility (the bulk-action bar clears on success).
 *
 * Server state only — the optimistic write lives in the TanStack Query cache, never
 * Zustand. The POST is workspace-scoped + member-gated server-side (B7).
 */
export function useBulkChangeSignalStatus() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<
    BulkStatusChangeRead,
    Error,
    BulkChangeStatusVars,
    BulkStatusRollback
  >({
    mutationFn: ({ signalIds, status }) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return changeSignalStatusBulk(token, workspaceId, signalIds, status);
    },
    onMutate: async ({ signalIds, status }) => {
      await queryClient.cancelQueries({
        queryKey: feedKeys.workspace(workspaceId),
      });
      const feed = queryClient.getQueriesData<InfiniteData<FeedPage>>({
        queryKey: [...feedKeys.workspace(workspaceId), "list"],
      });
      const ids = new Set(signalIds);
      for (const [key] of feed) {
        queryClient.setQueryData<InfiniteData<FeedPage>>(key, (old) =>
          patchFeedStatusMany(old, ids, status),
        );
      }
      return { feed };
    },
    onError: (_err, _vars, context) => {
      if (!context) return;
      for (const [key, snapshot] of context.feed) {
        queryClient.setQueryData(key, snapshot);
      }
    },
    onSettled: () => {
      // Re-sync from the server: only some rows may have transitioned (partial
      // failure), and the feed ordering / visibility can shift (dismissed rows drop
      // out of the default visible set).
      void queryClient.invalidateQueries({
        queryKey: feedKeys.workspace(workspaceId),
      });
    },
  });
}

// ---- useSignalFeedback (F5, doc 14 §12) -------------------------------------

/**
 * Variables for the F5 feedback mutation: which signal, and the target verdict —
 * or ``null`` to retract the current verdict.
 */
export interface SignalFeedbackVars {
  signalId: string;
  kind: FeedbackKind | null;
}

/** Snapshot of the detail cache we touched, kept so onError can roll back. */
interface FeedbackRollback {
  detail: SignalDetailRead | undefined;
}

/**
 * Mutation hook for the F5 per-signal relevance feedback (mark relevant / not
 * relevant / wrong extraction, or retract). Optimistically patches the signal's
 * ``feedback`` in the detail cache, rolls back on error, and invalidates the detail
 * query on settle so the server stays the source of truth (doc 06 §2).
 *
 * Passing ``kind: null`` retracts the current verdict (DELETE); a non-null kind
 * records / changes it (POST). Server state only — the optimistic write lives in the
 * TanStack Query cache, never Zustand. Workspace-scoped + member-gated server-side (B7).
 */
export function useSignalFeedback() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<
    FeedbackRead,
    Error,
    SignalFeedbackVars,
    FeedbackRollback
  >({
    mutationFn: ({ signalId, kind }) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return kind === null
        ? retractSignalFeedback(token, workspaceId, signalId)
        : submitSignalFeedback(token, workspaceId, signalId, kind);
    },
    onMutate: async ({ signalId, kind }) => {
      const detailKey = feedKeys.detail(workspaceId, signalId);
      await queryClient.cancelQueries({ queryKey: detailKey });
      const detail = queryClient.getQueryData<SignalDetailRead>(detailKey);
      if (detail) {
        queryClient.setQueryData<SignalDetailRead>(detailKey, {
          ...detail,
          feedback: kind,
        });
      }
      return { detail };
    },
    onError: (_err, { signalId }, context) => {
      if (context?.detail) {
        queryClient.setQueryData(
          feedKeys.detail(workspaceId, signalId),
          context.detail,
        );
      }
    },
    onSettled: (_data, _err, { signalId }) => {
      void queryClient.invalidateQueries({
        queryKey: feedKeys.detail(workspaceId, signalId),
      });
    },
  });
}
