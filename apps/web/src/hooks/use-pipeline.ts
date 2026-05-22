// Pipeline hooks (J5, J4) — TanStack Query owns server state (doc 06 §2).
//
// usePipelineReport      — fetch the workspace-level pipeline rollup report (J5).
// useCreatePipelineItem  — mutation: create a manual pipeline item (J4).
//
// All hooks are workspace-scoped: they skip when token or workspaceId are absent.

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createManualPipelineItem,
  getPipelineReport,
  type ManualPipelineItemCreate,
  type PipelineItem,
  type PipelineReport,
} from "@/lib/pipeline-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

// ---- Auth/workspace helpers ----

function useAuth() {
  const token = useSessionStore((s) => s.accessToken);
  const workspaceId = useUiStore((s) => s.activeWorkspaceId);
  return { token, workspaceId };
}

// ---- Query keys ----

export const pipelineKeys = {
  all: ["pipeline"] as const,
  report: (workspaceId: string) =>
    [...pipelineKeys.all, "report", workspaceId] as const,
  items: (workspaceId: string) =>
    [...pipelineKeys.all, "items", workspaceId] as const,
};

// ---- usePipelineReport ----

/**
 * Fetch the workspace-level pipeline rollup report.
 * Returns per-stage item counts + summed value_estimate, plus workspace totals.
 * Skipped when token or workspaceId are absent.
 */
export function usePipelineReport(
  token: string | null,
  workspaceId: string | null,
) {
  return useQuery<PipelineReport>({
    queryKey: pipelineKeys.report(workspaceId ?? ""),
    queryFn: () => getPipelineReport(token!, workspaceId!),
    enabled: token !== null && workspaceId !== null,
  });
}

// ---- useCreatePipelineItem ----

/**
 * Create a manual pipeline item (J4).
 * Calls POST /pipeline/items/manual and invalidates the items list and report
 * on success so any list views stay current.
 */
export function useCreatePipelineItem() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<PipelineItem, Error, ManualPipelineItemCreate>({
    mutationFn: (input) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return createManualPipelineItem(token, workspaceId, input);
    },
    onSuccess: () => {
      // Invalidate items list and report so they refetch with the new item.
      void queryClient.invalidateQueries({
        queryKey: pipelineKeys.items(workspaceId ?? ""),
      });
      void queryClient.invalidateQueries({
        queryKey: pipelineKeys.report(workspaceId ?? ""),
      });
    },
  });
}
