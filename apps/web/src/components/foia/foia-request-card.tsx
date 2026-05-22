// FoiaRequestCard — compact row/card for the FOIA request list page (M4).

import Link from "next/link";
import type { FoiaRequestRead } from "@/lib/foia-api";
import { FoiaStatusBadge } from "./foia-status-badge";

interface FoiaRequestCardProps {
  request: FoiaRequestRead;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Return how many days have elapsed since the request was sent. */
function daysSinceSent(sentAt: string | null): number | null {
  if (!sentAt) return null;
  const ms = Date.now() - new Date(sentAt).getTime();
  return Math.floor(ms / (1000 * 60 * 60 * 24));
}

export function FoiaRequestCard({ request }: FoiaRequestCardProps) {
  const days = daysSinceSent(request.sent_at);

  return (
    <article
      className="group flex flex-col gap-2 rounded-lg border bg-card px-5 py-4 shadow-sm transition-shadow hover:shadow-md focus-within:ring-2 focus-within:ring-ring"
      aria-label={request.subject}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <Link
          href={`/foia/${request.id}`}
          className="text-base font-semibold leading-snug text-foreground hover:underline focus-visible:outline-none focus-visible:underline"
        >
          {request.subject}
        </Link>
        <FoiaStatusBadge status={request.status} />
      </div>

      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-muted-foreground">
        {request.jurisdiction && (
          <>
            <span>{request.jurisdiction}</span>
            <span aria-hidden>·</span>
          </>
        )}
        <span>Created {formatDate(request.created_at)}</span>

        {request.status === "sent" && days !== null && (
          <>
            <span aria-hidden>·</span>
            <span>Awaiting ({days}d)</span>
          </>
        )}

        {request.status === "ack" && request.ack_at && (
          <>
            <span aria-hidden>·</span>
            <span>Acknowledged {formatDate(request.ack_at)}</span>
          </>
        )}

        {request.status === "response" && request.response_at && (
          <>
            <span aria-hidden>·</span>
            <span>Responded {formatDate(request.response_at)}</span>
          </>
        )}

        {request.submission_target && (
          <>
            <span aria-hidden>·</span>
            <span className="truncate max-w-[240px]">{request.submission_target}</span>
          </>
        )}
      </div>
    </article>
  );
}
