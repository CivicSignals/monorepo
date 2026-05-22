// FoiaDetail — client island for the /foia/[id] detail page (M4).
//
// Displays: subject, body, status, jurisdiction, submission details, timeline.
// Shows transition controls respecting the state machine.

"use client";

import Link from "next/link";
import { useFoiaRequest, useFoiaEvents } from "@/hooks/use-foia";
import { FoiaStatusBadge } from "@/components/foia/foia-status-badge";
import { FoiaTimeline } from "@/components/foia/foia-timeline";
import { FoiaTransitionControls } from "@/components/foia/foia-transition-controls";
import { ProblemError } from "@/lib/auth-api";
import type { FoiaStatus } from "@/lib/foia-api";

interface FoiaDetailProps {
  id: string;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

function FieldRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-2 text-sm">
      <dt className="font-medium text-muted-foreground">{label}</dt>
      <dd className="text-foreground">{value}</dd>
    </div>
  );
}

export function FoiaDetail({ id }: FoiaDetailProps) {
  const { data: req, isLoading, error } = useFoiaRequest(id);
  const { data: events = [], isLoading: eventsLoading } = useFoiaEvents(id);

  if (isLoading) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Loading FOIA request">
        <div className="h-8 w-2/3 animate-pulse rounded bg-muted" />
        <div className="h-4 w-1/3 animate-pulse rounded bg-muted" />
        <div className="h-40 animate-pulse rounded bg-muted" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        role="alert"
        className="rounded-lg border border-destructive/50 bg-destructive/10 px-5 py-4 text-sm text-destructive"
      >
        {error instanceof ProblemError
          ? (error.problem.detail ?? error.problem.title)
          : error.message}
      </div>
    );
  }

  if (!req) {
    return (
      <div className="py-16 text-center text-muted-foreground">
        <p className="text-lg font-medium">FOIA request not found.</p>
        <Link href="/foia" className="mt-2 text-sm underline hover:no-underline">
          Back to FOIA requests
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Back link */}
      <Link
        href="/foia"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        ← Back to FOIA requests
      </Link>

      {/* Header */}
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold tracking-tight break-words">{req.subject}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <FoiaStatusBadge status={req.status as FoiaStatus} />
            {req.jurisdiction && (
              <span className="text-sm text-muted-foreground">{req.jurisdiction}</span>
            )}
          </div>
        </div>
      </div>

      {/* Metadata */}
      <section aria-label="Request details">
        <dl className="space-y-2">
          <FieldRow label="Status" value={<FoiaStatusBadge status={req.status as FoiaStatus} />} />
          <FieldRow label="Created" value={formatDate(req.created_at)} />
          {req.sent_at && <FieldRow label="Sent" value={formatDate(req.sent_at)} />}
          {req.ack_at && <FieldRow label="Acknowledged" value={formatDate(req.ack_at)} />}
          {req.response_at && <FieldRow label="Response received" value={formatDate(req.response_at)} />}
          <FieldRow
            label="Submission"
            value={
              req.submission_target ? (
                <span>
                  {req.submission_method} — {req.submission_target}
                </span>
              ) : (
                req.submission_method
              )
            }
          />
          <FieldRow label="Target entity" value={<code className="text-xs">{req.entity_id}</code>} />
        </dl>
      </section>

      {/* Body */}
      <section aria-label="Request body">
        <h2 className="mb-2 text-base font-semibold">Request body</h2>
        <pre className="whitespace-pre-wrap rounded-lg border bg-muted/30 p-4 text-sm leading-relaxed font-sans">
          {req.body}
        </pre>
      </section>

      {/* Response notes */}
      {req.response_notes && (
        <section aria-label="Response notes">
          <h2 className="mb-2 text-base font-semibold">Response notes</h2>
          <p className="rounded-lg border bg-muted/30 p-4 text-sm">{req.response_notes}</p>
        </section>
      )}

      {/* Status transitions */}
      <section aria-label="Status controls">
        <h2 className="mb-3 text-base font-semibold">Advance status</h2>
        <FoiaTransitionControls
          requestId={req.id}
          currentStatus={req.status as FoiaStatus}
        />
      </section>

      {/* Timeline */}
      <section aria-label="Status timeline">
        <h2 className="mb-3 text-base font-semibold">Timeline</h2>
        <FoiaTimeline events={events} isLoading={eventsLoading} />
      </section>
    </div>
  );
}
