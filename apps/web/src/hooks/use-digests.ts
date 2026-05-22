// Saved-search digest hooks (H3) — TanStack Query owns the server state (doc 06 §2).
//
// Digests are per (saved search, current user) and workspace-scoped; query keys
// include workspaceId + savedSearchId so cached data never bleeds across
// workspace switches (doc 08 §1.4, B5).
//
// useDigest    — read the caller's digest for a saved search (null = off/unset)
// useSetDigest — mutation: PUT /notifications/digests/{savedSearchId}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type DigestSubscription,
  type DigestSubscriptionListItem,
  type DigestSubscriptionUpsert,
  type UnsubscribeResult,
  getDigest,
  listUserDigests,
  setDigest,
  unsubscribeWithToken,
} from "@/lib/digests-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

function useAuth() {
  const token = useSessionStore((s) => s.accessToken);
  const workspaceId = useUiStore((s) => s.activeWorkspaceId);
  return { token, workspaceId };
}

export const digestKeys = {
  all: ["digests"] as const,
  workspace: (workspaceId: string | null) =>
    [...digestKeys.all, workspaceId ?? "none"] as const,
  detail: (workspaceId: string | null, savedSearchId: string) =>
    [...digestKeys.workspace(workspaceId), savedSearchId] as const,
  list: (workspaceId: string | null) =>
    [...digestKeys.workspace(workspaceId), "list"] as const,
};

/**
 * The current user's digest subscriptions in the active workspace (H5). Powers the
 * consolidated /settings/notifications preferences page.
 */
export function useUserDigests() {
  const { token, workspaceId } = useAuth();
  return useQuery<DigestSubscriptionListItem[]>({
    queryKey: digestKeys.list(workspaceId),
    queryFn: () => {
      if (!token || !workspaceId) return Promise.resolve([]);
      return listUserDigests(token, workspaceId);
    },
    enabled: token !== null && workspaceId !== null,
  });
}

/** Read the caller's digest schedule for one saved search (null = none/off). */
export function useDigest(savedSearchId: string) {
  const { token, workspaceId } = useAuth();
  return useQuery<DigestSubscription | null>({
    queryKey: digestKeys.detail(workspaceId, savedSearchId),
    queryFn: () => {
      if (!token || !workspaceId) return Promise.resolve(null);
      return getDigest(token, workspaceId, savedSearchId);
    },
    enabled: token !== null && workspaceId !== null,
  });
}

/** Set the caller's digest schedule for a saved search. Refreshes the detail. */
export function useSetDigest(savedSearchId: string) {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<DigestSubscription, Error, DigestSubscriptionUpsert>({
    mutationFn: (input) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return setDigest(token, workspaceId, savedSearchId, input);
    },
    onSuccess: (data) => {
      queryClient.setQueryData(
        digestKeys.detail(workspaceId, savedSearchId),
        data,
      );
      // Keep the consolidated prefs list (H5) in sync after a per-row change.
      void queryClient.invalidateQueries({
        queryKey: digestKeys.list(workspaceId),
      });
    },
  });
}

/**
 * One-click unsubscribe via the signed token from a digest email (H5). No auth /
 * workspace context — the token is the authorization. The confirm page calls
 * `mutate(token)` after the recipient confirms.
 */
export function useUnsubscribe() {
  return useMutation<UnsubscribeResult, Error, string>({
    mutationFn: (token) => unsubscribeWithToken(token),
  });
}
