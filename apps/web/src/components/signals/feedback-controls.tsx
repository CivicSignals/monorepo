// FeedbackControls — per-signal relevance feedback buttons (F5, doc 14 §12).
//
// Renders the three feedback verdicts (Relevant / Not relevant / Wrong extraction)
// for one signal, wired to the useSignalFeedback mutation (TanStack Query, optimistic
// update — doc 06 §2). Clicking the already-active verdict retracts it (kind: null).
//
// relevant / not_relevant re-weight subsequent scoring for the workspace; the
// wrong_extraction verdict flags extraction quality and does NOT change scoring (the
// server records + surfaces it for the QA-7 / E-epic review). Used on the signal
// detail page; the same control can drop onto a feed row.

"use client";

import {
  type FeedbackKind,
  FEEDBACK_KIND_LABELS,
} from "@/lib/signals-api";
import { useSignalFeedback } from "@/hooks/use-signals";

// The verdicts in the order they render.
const FEEDBACK_ORDER: FeedbackKind[] = [
  "relevant",
  "not_relevant",
  "wrong_extraction",
];

// Per-verdict button styling. The active verdict gets a filled treatment; the rest
// stay outlined. ``not_relevant`` reads cautionary, ``wrong_extraction`` neutral.
const ACTIVE_CLASS: Record<FeedbackKind, string> = {
  relevant: "bg-green-600 text-white border-green-600",
  not_relevant: "bg-amber-600 text-white border-amber-600",
  wrong_extraction: "bg-gray-700 text-white border-gray-700",
};

const IDLE_CLASS: Record<FeedbackKind, string> = {
  relevant: "border-green-200 text-green-700 hover:bg-green-50",
  not_relevant: "border-amber-200 text-amber-700 hover:bg-amber-50",
  wrong_extraction: "border-gray-200 text-gray-600 hover:bg-gray-50",
};

export interface FeedbackControlsProps {
  signalId: string;
  /** The calling user's current verdict, or null if none. */
  current: FeedbackKind | null;
  size?: "sm" | "md";
}

/**
 * Relevance feedback controls for one signal (F5).
 *
 * One button per verdict; clicking a new verdict records it, clicking the active one
 * retracts it. Fires the optimistic mutation; while in flight the buttons are
 * disabled. Errors surface inline (the optimistic write rolls back via the hook).
 */
export function FeedbackControls({
  signalId,
  current,
  size = "md",
}: FeedbackControlsProps) {
  const mutation = useSignalFeedback();

  const base =
    size === "sm" ? "text-[11px] px-2 py-0.5" : "text-xs px-2.5 py-1";

  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      role="group"
      aria-label="Signal relevance feedback"
      data-testid="feedback-controls"
    >
      <span className="text-xs text-muted-foreground mr-1">Feedback:</span>
      {FEEDBACK_ORDER.map((kind) => {
        const active = current === kind;
        const cls = active ? ACTIVE_CLASS[kind] : IDLE_CLASS[kind];
        return (
          <button
            key={kind}
            type="button"
            data-testid={`feedback-action-${kind}`}
            aria-pressed={active}
            disabled={mutation.isPending}
            // Toggle: clicking the active verdict retracts it (kind: null).
            onClick={() =>
              mutation.mutate({ signalId, kind: active ? null : kind })
            }
            className={`rounded-md border font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500 ${base} ${cls}`}
          >
            {FEEDBACK_KIND_LABELS[kind]}
          </button>
        );
      })}
      {mutation.isError && (
        <span
          data-testid="feedback-error"
          role="alert"
          className="text-[11px] text-red-500"
        >
          {mutation.error?.message ?? "Could not save feedback"}
        </span>
      )}
    </div>
  );
}
