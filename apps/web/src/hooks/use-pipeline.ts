// Pipeline hooks (J5) — TanStack Query owns server state (doc 06 §2).
//
// usePipelineReport   — fetch the workspace-level pipeline rollup report.
//
// All hooks are workspace-scoped: they skip when token or workspaceId are absent.

"use client";

import { useQuery } from "@tanstack/react-query";
import { getPipelineReport, type PipelineReport } from "@/lib/pipeline-api";

// ---- Query keys ----

export const pipelineKeys = {
  all: ["pipeline"] as const,
  report: (workspaceId: string) =>
    [...pipelineKeys.all, "report", workspaceId] as const,
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
