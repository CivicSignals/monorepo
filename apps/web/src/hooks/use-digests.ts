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
  type DigestSubscriptionUpsert,
  getDigest,
  setDigest,
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
};

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
    },
  });
}
