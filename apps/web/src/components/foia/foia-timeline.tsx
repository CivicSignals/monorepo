// FoiaTimeline — vertical status-transition history for the detail page (M4).

import type { FoiaRequestEventRead } from "@/lib/foia-api";

interface FoiaTimelineProps {
  events: FoiaRequestEventRead[];
  isLoading?: boolean;
}

const STATUS_LABELS: Record<string, string> = {
  draft: "Draft",
  sent: "Awaiting response",
  ack: "Acknowledged",
  response: "Response received",
};

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function FoiaTimeline({ events, isLoading }: FoiaTimelineProps) {
  if (isLoading) {
    return (
      <div className="space-y-3" aria-busy="true" aria-label="Loading timeline">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="h-10 animate-pulse rounded bg-muted" />
        ))}
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No transitions yet — this request is still in its initial state.
      </p>
    );
  }

  return (
    <ol className="relative space-y-4 border-l border-muted pl-6" aria-label="Status timeline">
      {events.map((ev, idx) => (
        <li key={ev.id} className="relative" aria-label={`Transitioned to ${STATUS_LABELS[ev.to_status] ?? ev.to_status}`}>
          {/* Timeline dot */}
          <span
            className="absolute -left-[1.375rem] top-1 h-3 w-3 rounded-full border-2 border-background bg-primary"
            aria-hidden
          />
          <p className="text-sm font-medium text-foreground">
            → {STATUS_LABELS[ev.to_status] ?? ev.to_status}
          </p>
          <p className="text-xs text-muted-foreground">
            {formatDateTime(ev.occurred_at)}
            {idx === 0 && (
              <span className="ml-2 italic">from {STATUS_LABELS[ev.from_status] ?? ev.from_status}</span>
            )}
          </p>
        </li>
      ))}
    </ol>
  );
}
