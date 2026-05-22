// FoiaTransitionControls — state-machine transition buttons for the detail page (M4).
//
// Shows only the allowed next transitions for the current status; illegal transitions
// are absent (not disabled) to avoid confusion. Terminal status (response) shows nothing.

"use client";

import { ALLOWED_NEXT, type FoiaStatus } from "@/lib/foia-api";
import { useTransitionFoiaRequest } from "@/hooks/use-foia";
import { ProblemError } from "@/lib/auth-api";

const TRANSITION_LABELS: Record<FoiaStatus, string> = {
  draft: "Mark as sent",
  sent: "Mark acknowledged",
  ack: "Mark as responded",
  response: "",
};

const TRANSITION_DESCRIPTIONS: Record<FoiaStatus, string> = {
  draft: "Advance to 'Awaiting' once you have sent the request.",
  sent: "Advance to 'Acknowledged' once the agency confirms receipt.",
  ack: "Advance to 'Response received' once the agency provides records.",
  response: "",
};

interface FoiaTransitionControlsProps {
  requestId: string;
  currentStatus: FoiaStatus;
  /** Called after a successful transition so the parent can refresh. */
  onTransitioned?: (newStatus: FoiaStatus) => void;
}

export function FoiaTransitionControls({
  requestId,
  currentStatus,
  onTransitioned,
}: FoiaTransitionControlsProps) {
  const mutation = useTransitionFoiaRequest(requestId);
  const nextStatuses = ALLOWED_NEXT[currentStatus] ?? [];

  if (nextStatuses.length === 0) {
    return (
      <p className="text-sm text-muted-foreground italic">
        This request has reached its final status.
      </p>
    );
  }

  const errorMessage = mutation.error
    ? mutation.error instanceof ProblemError
      ? (mutation.error.problem.detail ?? mutation.error.problem.title)
      : mutation.error.message
    : null;

  return (
    <div className="space-y-3">
      {nextStatuses.map((nextStatus) => (
        <div key={nextStatus} className="flex flex-col gap-1">
          <button
            type="button"
            aria-label={`Transition to ${nextStatus}`}
            disabled={mutation.isPending}
            onClick={() => {
              mutation.mutate(
                { status: nextStatus },
                {
                  onSuccess: () => onTransitioned?.(nextStatus),
                },
              );
            }}
            className="inline-flex w-fit items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
          >
            {mutation.isPending ? "Updating…" : TRANSITION_LABELS[nextStatus]}
          </button>
          <p className="text-xs text-muted-foreground">{TRANSITION_DESCRIPTIONS[nextStatus]}</p>
        </div>
      ))}

      {errorMessage && (
        <div
          role="alert"
          className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {errorMessage}
        </div>
      )}
    </div>
  );
}
