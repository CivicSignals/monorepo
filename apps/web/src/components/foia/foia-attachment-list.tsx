// FoiaAttachmentList — displays uploaded response documents for a FOIA request
// with their extraction status + linked signals (M3).

"use client";

import { useState } from "react";
import { useFoiaAttachments, useFoiaAttachmentSignals } from "@/hooks/use-foia";
import type { FoiaAttachmentRead } from "@/lib/foia-api";

const STATUS_LABEL: Record<string, string> = {
  pending: "Queued",
  running: "Extracting…",
  done: "Done",
  failed: "Failed",
  skipped: "Skipped (irrelevant)",
};

const STATUS_CLASS: Record<string, string> = {
  pending: "bg-muted text-muted-foreground",
  running: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  done: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  failed: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300",
  skipped: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

interface AttachmentSignalsProps {
  requestId: string;
  attachmentId: string;
}

function AttachmentSignals({ requestId, attachmentId }: AttachmentSignalsProps) {
  const { data: signals, isLoading } = useFoiaAttachmentSignals(requestId, attachmentId);

  if (isLoading) return <p className="text-xs text-muted-foreground">Loading signals…</p>;
  if (!signals || signals.length === 0) {
    return <p className="text-xs text-muted-foreground">No signals yet.</p>;
  }

  return (
    <ul className="space-y-1.5">
      {signals.map((sig) => (
        <li key={sig.id} className="flex items-start gap-2 text-xs">
          <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
            {sig.signal_type}
          </span>
          <span className="flex-1 text-foreground">{sig.title}</span>
          {sig.confidence !== null && (
            <span className="shrink-0 text-muted-foreground">
              {Math.round(sig.confidence * 100)}%
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

interface AttachmentRowProps {
  requestId: string;
  attachment: FoiaAttachmentRead;
}

function AttachmentRow({ requestId, attachment: att }: AttachmentRowProps) {
  const [expanded, setExpanded] = useState(false);
  const isDone = att.extraction_status === "done";

  return (
    <li className="rounded-lg border bg-card p-3 text-sm">
      <div className="flex flex-wrap items-start gap-2">
        {/* filename */}
        <span className="flex-1 font-medium text-foreground truncate" title={att.filename}>
          {att.filename}
        </span>

        {/* extraction status badge */}
        <span
          className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[att.extraction_status] ?? STATUS_CLASS.pending}`}
        >
          {STATUS_LABEL[att.extraction_status] ?? att.extraction_status}
        </span>
      </div>

      <p className="mt-1 text-xs text-muted-foreground">
        Uploaded {formatDate(att.uploaded_at)} · {att.content_type}
      </p>

      {/* show/hide signals toggle (only when done) */}
      {isDone && (
        <div className="mt-2">
          <button
            type="button"
            className="text-xs text-primary underline hover:no-underline"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-controls={`signals-${att.id}`}
          >
            {expanded ? "Hide signals" : "Show linked signals"}
          </button>
          {expanded && (
            <div id={`signals-${att.id}`} className="mt-2">
              <AttachmentSignals requestId={requestId} attachmentId={att.id} />
            </div>
          )}
        </div>
      )}
    </li>
  );
}

interface FoiaAttachmentListProps {
  requestId: string;
}

export function FoiaAttachmentList({ requestId }: FoiaAttachmentListProps) {
  const { data, isLoading, error } = useFoiaAttachments(requestId);

  if (isLoading) {
    return (
      <div aria-busy="true" className="space-y-2">
        {[0, 1].map((i) => (
          <div key={i} className="h-14 animate-pulse rounded-lg bg-muted" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error instanceof Error ? error.message : "Failed to load attachments."}
      </p>
    );
  }

  const items = data?.items ?? [];
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No response documents uploaded yet.
      </p>
    );
  }

  return (
    <ul className="space-y-2" aria-label="Uploaded response documents">
      {items.map((att) => (
        <AttachmentRow key={att.id} requestId={requestId} attachment={att} />
      ))}
    </ul>
  );
}
