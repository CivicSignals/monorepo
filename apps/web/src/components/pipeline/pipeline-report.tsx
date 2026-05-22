// PipelineReport — workspace-level pipeline rollup view (J5).
//
// Shows per-stage item counts as a CSS bar chart (no extra chart deps), plus a
// summary row (total items, total pipeline value). TanStack Query drives the
// data fetch; loading / empty / error states are handled inline.
//
// Design notes:
// - Bar heights are relative to the stage with the most items so the chart
//   scales to any count range.
// - value_estimate is expressed in USD; displayed as formatted currency.
// - The component is purely presentational: it accepts the PipelineReport data
//   as a prop so the parent (or test) can inject any data shape.

"use client";

import type { PipelineReport, StageRollup } from "@/lib/pipeline-api";

// ---- Helpers ----------------------------------------------------------------

function formatUSD(value: string | null): string {
  if (value === null) return "—";
  const num = parseFloat(value);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(num);
}

// ---- Sub-components ---------------------------------------------------------

function BarChart({ stages }: { stages: StageRollup[] }) {
  const maxCount = Math.max(...stages.map((s) => s.item_count), 1);

  return (
    <div
      role="img"
      aria-label="Pipeline stage bar chart"
      className="flex items-end gap-2 h-48 border-b border-gray-200 pb-2"
    >
      {stages.map((stage) => {
        const heightPct = (stage.item_count / maxCount) * 100;
        return (
          <div
            key={stage.stage_id}
            className="flex flex-col items-center flex-1 min-w-0 group"
          >
            <span className="text-xs text-gray-500 mb-1 font-medium tabular-nums">
              {stage.item_count}
            </span>
            <div
              data-testid={`bar-${stage.stage_name}`}
              className="w-full bg-indigo-500 group-hover:bg-indigo-600 rounded-t transition-colors"
              style={{ height: `${Math.max(heightPct, stage.item_count > 0 ? 4 : 2)}%` }}
              title={`${stage.stage_name}: ${stage.item_count} item${stage.item_count !== 1 ? "s" : ""}${stage.total_value ? `, ${formatUSD(stage.total_value)}` : ""}`}
            />
            <span className="text-[10px] text-gray-400 mt-1 truncate w-full text-center leading-tight">
              {stage.stage_name}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function StageSummaryTable({ stages }: { stages: StageRollup[] }) {
  return (
    <table className="w-full text-sm mt-4" aria-label="Stage summary table">
      <thead>
        <tr className="text-left text-gray-500 border-b border-gray-100">
          <th className="py-1 pr-4 font-medium">Stage</th>
          <th className="py-1 pr-4 font-medium text-right">Items</th>
          <th className="py-1 font-medium text-right">Value</th>
        </tr>
      </thead>
      <tbody>
        {stages.map((stage) => (
          <tr
            key={stage.stage_id}
            className="border-b border-gray-50 hover:bg-gray-50"
          >
            <td className="py-1.5 pr-4 text-gray-800">{stage.stage_name}</td>
            <td className="py-1.5 pr-4 text-right tabular-nums text-gray-700">
              {stage.item_count}
            </td>
            <td className="py-1.5 text-right tabular-nums text-gray-700">
              {formatUSD(stage.total_value)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---- Public component -------------------------------------------------------

interface PipelineReportViewProps {
  report: PipelineReport;
}

/** Presentational pipeline report — inject data via props. */
export function PipelineReportView({ report }: PipelineReportViewProps) {
  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-2">
        <div
          className="rounded-lg border border-gray-200 p-4"
          data-testid="summary-total-items"
        >
          <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">
            Total items
          </p>
          <p className="mt-1 text-3xl font-semibold tabular-nums text-gray-900">
            {report.total_items}
          </p>
        </div>
        <div
          className="rounded-lg border border-gray-200 p-4"
          data-testid="summary-total-value"
        >
          <p className="text-xs text-gray-500 uppercase tracking-wide font-medium">
            Total value
          </p>
          <p className="mt-1 text-3xl font-semibold tabular-nums text-gray-900">
            {formatUSD(report.total_value)}
          </p>
        </div>
      </div>

      {/* Bar chart */}
      {report.stages.length > 0 ? (
        <BarChart stages={report.stages} />
      ) : (
        <p className="text-sm text-gray-400 italic">No stages configured.</p>
      )}

      {/* Stage-by-stage table */}
      <StageSummaryTable stages={report.stages} />
    </div>
  );
}
