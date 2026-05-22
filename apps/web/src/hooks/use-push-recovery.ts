// Push-failure recovery hooks (K5) — TanStack Query owns server state (doc 06 §2).
//
// Drives the recovery surface in integrations settings: list recent failed pushes
// (with inline diagnosis), and retry one. Retry routes through the K4 idempotent
// path server-side, so a retry of a push that already succeeded won't duplicate.
// All endpoints are workspace-scoped + gated at RequireMember by the API.

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type PushFailureEntry,
  type PushLogEntry,
  listPushFailures,
  retryPush,
} from "@/lib/salesforce-api";
import { useSessionStore } from "@/store/session";

export const pushFailuresKey = (workspaceId: string | undefined) =>
  ["integrations", "push-failures", workspaceId ?? "none"] as const;

/** List the workspace's recent failed pushes, each with an inline diagnosis. */
export function usePushFailures(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<PushFailureEntry[]>({
    queryKey: pushFailuresKey(workspaceId),
    queryFn: async () => {
      if (!token || !workspaceId) return [];
      return listPushFailures(token, workspaceId);
    },
    enabled: token !== null && !!workspaceId,
  });
}

/** Retry a failed push by its push-log id; invalidates the failures list. */
export function useRetryPush(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<PushLogEntry, Error, string>({
    mutationFn: (pushLogId) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("no active workspace"));
      return retryPush(token, workspaceId, pushLogId);
    },
    onSuccess: () => {
      // A successful retry clears the row from the failures list; a still-failed
      // retry appends a new failed row — either way, re-fetch the list.
      void queryClient.invalidateQueries({
        queryKey: pushFailuresKey(workspaceId),
      });
    },
  });
}
