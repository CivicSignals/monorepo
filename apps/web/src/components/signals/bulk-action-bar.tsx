// BulkActionBar — multi-select mass actions for the feed (G3).
//
// Appears when ≥ 1 feed row is selected. Offers mass Pin / mass Dismiss, wired to the
// useBulkChangeSignalStatus mutation (TanStack Query, optimistic update — doc 06 §2).
// The bar is workspace-scoped + member-gated server-side (B7); it clears the selection
// after a successful bulk action (the parent owns the selection state).
//
// Mass actions apply one target status to every selected signal. The backend reuses the
// G4 transition graph per item and is resilient: a row whose current status forbids the
// move is skipped server-side rather than failing the batch, and the feed re-syncs on
// settle (doc 14 §5.3).

"use client";

import type { SettableStatus } from "@/lib/signals-api";
import { useBulkChangeSignalStatus } from "@/hooks/use-signals";

export interface BulkActionBarProps {
  /** The currently-selected signal ids (the parent owns this state). */
  selectedIds: string[];
  /** Clear the selection (called after a successful bulk action). */
  onClear: () => void;
}

/**
 * Bulk-action bar for the multi-select feed (G3).
 *
 * Renders nothing when the selection is empty. Otherwise shows the selected count, a
 * Pin / Dismiss action pair, and a Clear button. Each action fires the bulk mutation;
 * while in flight the buttons are disabled. On success the selection is cleared; on
 * error the message surfaces inline (the optimistic write rolls back via the hook).
 */
export function BulkActionBar({ selectedIds, onClear }: BulkActionBarProps) {
  const mutation = useBulkChangeSignalStatus();
  const count = selectedIds.length;
  if (count === 0) return null;

  const apply = (status: SettableStatus) => {
    mutation.mutate(
      { signalIds: selectedIds, status },
      { onSuccess: () => onClear() },
    );
  };

  return (
    <div
      data-testid="bulk-action-bar"
      role="region"
      aria-label="Bulk actions"
      className="sticky top-2 z-10 flex flex-wrap items-center gap-3 rounded-lg border border-indigo-200 bg-indigo-50 px-4 py-2.5 shadow-sm"
    >
      <span
        data-testid="bulk-selected-count"
        className="text-sm font-medium text-indigo-900"
      >
        {count} selected
      </span>

      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid="bulk-action-pinned"
          disabled={mutation.isPending}
          onClick={() => apply("pinned")}
          className="rounded-md border border-indigo-300 bg-white px-3 py-1 text-xs font-medium text-indigo-700 transition-colors hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500"
        >
          Pin
        </button>
        <button
          type="button"
          data-testid="bulk-action-dismissed"
          disabled={mutation.isPending}
          onClick={() => apply("dismissed")}
          className="rounded-md border border-gray-300 bg-white px-3 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-100 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500"
        >
          Dismiss
        </button>
      </div>

      <button
        type="button"
        data-testid="bulk-clear"
        onClick={onClear}
        disabled={mutation.isPending}
        className="ml-auto text-xs font-medium text-gray-500 hover:text-gray-700 disabled:opacity-50"
      >
        Clear
      </button>

      {mutation.isError && (
        <span
          data-testid="bulk-error"
          role="alert"
          className="w-full text-xs text-red-500"
        >
          {mutation.error?.message ?? "Could not update signals"}
        </span>
      )}
    </div>
  );
}
