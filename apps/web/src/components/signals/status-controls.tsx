// StatusControls — per-signal status transition buttons (G4).
//
// Renders the triage actions (Mark reviewed / Pin / Dismiss / Restore) for one
// signal's per-workspace status, wired to the useChangeSignalStatus mutation
// (TanStack Query, optimistic update — doc 06 §2). Used on both the feed row
// (compact) and the signal detail header.
//
// The allowed transitions mirror the backend graph (signals.services
// .STATUS_TRANSITIONS, doc 14 §5.3): new -> reviewed/pinned/dismissed;
// reviewed -> pinned/dismissed; pinned -> reviewed/dismissed;
// pushed -> pinned/dismissed; dismissed -> new (restore). ``pushed`` is never
// settable here (it is owned by the K-epic push flow).

"use client";

import type { FeedStatus, SettableStatus } from "@/lib/signals-api";
import { useChangeSignalStatus } from "@/hooks/use-signals";

// ---- Transition graph (mirror of the backend STATUS_TRANSITIONS) -----------

const ALLOWED_TARGETS: Record<FeedStatus, SettableStatus[]> = {
  new: ["reviewed", "pinned", "dismissed"],
  reviewed: ["pinned", "dismissed"],
  pinned: ["reviewed", "dismissed"],
  pushed: ["pinned", "dismissed"],
  dismissed: ["new"],
};

// Button label per target status. ``new`` from a dismissed row reads as "Restore".
const ACTION_LABELS: Record<SettableStatus, string> = {
  new: "Restore",
  reviewed: "Mark reviewed",
  pinned: "Pin",
  dismissed: "Dismiss",
};

export interface StatusControlsProps {
  signalId: string;
  status: FeedStatus;
  /** Compact (feed row) vs. full (detail header) presentation. */
  size?: "sm" | "md";
}

/**
 * Status transition controls for one signal (G4).
 *
 * Shows a button per allowed target transition from the current status. Clicking
 * fires the optimistic mutation; while in flight the buttons are disabled. Errors
 * surface inline (and the optimistic write rolls back via the hook).
 */
export function StatusControls({
  signalId,
  status,
  size = "md",
}: StatusControlsProps) {
  const mutation = useChangeSignalStatus();
  const targets = ALLOWED_TARGETS[status] ?? [];

  const base =
    size === "sm"
      ? "text-[11px] px-2 py-0.5"
      : "text-xs px-2.5 py-1";

  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      role="group"
      aria-label="Signal status actions"
      data-testid="status-controls"
    >
      {targets.map((target) => {
        const isDismiss = target === "dismissed";
        const cls = isDismiss
          ? "border-gray-200 text-gray-500 hover:bg-gray-50"
          : "border-indigo-200 text-indigo-700 hover:bg-indigo-50";
        return (
          <button
            key={target}
            type="button"
            data-testid={`status-action-${target}`}
            disabled={mutation.isPending}
            onClick={() => mutation.mutate({ signalId, status: target })}
            className={`rounded-md border font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 ${base} ${cls}`}
          >
            {ACTION_LABELS[target]}
          </button>
        );
      })}
      {mutation.isError && (
        <span
          data-testid="status-error"
          role="alert"
          className="text-[11px] text-red-500"
        >
          {mutation.error?.message ?? "Could not update status"}
        </span>
      )}
    </div>
  );
}
