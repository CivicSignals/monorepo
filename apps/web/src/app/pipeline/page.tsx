// Pipeline reporting page (J5) — /pipeline
//
// Shows the workspace-level pipeline rollup report: per-stage item counts +
// summed value_estimate displayed as a bar chart plus a summary table.
//
// State wiring:
//   - Token from Zustand session store (client-only, doc 06 §2).
//   - Active workspace id from Zustand UI store (client-only, doc 06 §2).
//   - Report data from TanStack Query (server state, doc 06 §2).

"use client";

import { usePipelineReport } from "@/hooks/use-pipeline";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import { PipelineReportView } from "@/components/pipeline/pipeline-report";

export default function PipelineReportPage() {
  const token = useSessionStore((s) => s.accessToken);
  const workspaceId = useUiStore((s) => s.activeWorkspaceId);
  const { data, isLoading, isError, error } = usePipelineReport(
    token,
    workspaceId,
  );

  return (
    <main className="max-w-4xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold text-gray-900 mb-6">
        Pipeline Report
      </h1>

      {/* Loading state */}
      {isLoading && (
        <div
          role="status"
          aria-label="Loading pipeline report"
          className="animate-pulse space-y-4"
        >
          <div className="h-32 bg-gray-100 rounded-lg" />
          <div className="h-48 bg-gray-100 rounded-lg" />
        </div>
      )}

      {/* Auth / workspace not configured */}
      {!isLoading && !token && (
        <p className="text-sm text-gray-500">
          Sign in to view your pipeline report.
        </p>
      )}
      {!isLoading && token && !workspaceId && (
        <p className="text-sm text-gray-500">
          Select a workspace to view its pipeline report.
        </p>
      )}

      {/* Error state */}
      {isError && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700"
        >
          Failed to load pipeline report:{" "}
          {error instanceof Error ? error.message : "Unknown error"}
        </div>
      )}

      {/* Empty state */}
      {!isLoading && !isError && data && data.total_items === 0 && (
        <div className="text-center py-12 text-gray-400">
          <p className="text-lg font-medium mb-1">No items in the pipeline</p>
          <p className="text-sm">
            Add items to your pipeline stages to see reporting here.
          </p>
        </div>
      )}

      {/* Report */}
      {!isLoading && !isError && data && data.total_items > 0 && (
        <PipelineReportView report={data} />
      )}
    </main>
  );
}
