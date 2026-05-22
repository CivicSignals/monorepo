// Push-failure recovery UI (K5; doc 04 J7 "diagnose + retry + reconnect").
//
// Lists the workspace's recent failed pushes with an inline, human-readable
// diagnosis derived from the scope-aware error category, a Retry button (TanStack
// Query mutation — retry routes through the K4 idempotent path so a retry of a
// push that already succeeded won't duplicate), and an expander linking to the
// full push-log entry.
//
// Mirrors the SalesforceSettings / SlackSettings settings-island pattern (K2/L1).
"use client";

import { useState } from "react";
import {
  usePushFailures,
  useRetryPush,
} from "@/hooks/use-push-recovery";
import type { PushFailureEntry } from "@/lib/salesforce-api";

export function PushRecovery({
  workspaceId,
}: {
  workspaceId: string | undefined;
}) {
  const failures = usePushFailures(workspaceId);

  if (!workspaceId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select or create a workspace first to review push failures.
      </p>
    );
  }

  return (
    <div className="space-y-4" data-testid="push-recovery">
      <p className="text-sm text-muted-foreground">
        Pushes to your connected CRMs and tools that failed recently. Retrying is
        safe — a push that actually succeeded will not be duplicated.
      </p>

      {failures.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading push failures…</p>
      ) : failures.isError ? (
        <p role="alert" className="text-sm text-destructive">
          Could not load push failures. Try again.
        </p>
      ) : (failures.data ?? []).length === 0 ? (
        <p
          className="text-sm text-muted-foreground"
          data-testid="no-push-failures"
        >
          No recent push failures. Everything is up to date.
        </p>
      ) : (
        <ul className="space-y-3">
          {(failures.data ?? []).map((failure) => (
            <FailureRow
              key={failure.id}
              workspaceId={workspaceId}
              failure={failure}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function FailureRow({
  workspaceId,
  failure,
}: {
  workspaceId: string;
  failure: PushFailureEntry;
}) {
  const retry = useRetryPush(workspaceId);
  const [expanded, setExpanded] = useState(false);

  const diagnosis = failure.diagnosis;
  // A non-retryable failure (validation/permission/not-found) needs a fix first;
  // a needs-reauth failure needs a reconnect. We still allow the click (the API
  // is the source of truth) but steer the operator with the recommended action.
  const canRetry = diagnosis?.retryable ?? true;

  return (
    <li
      className="rounded-lg border p-4"
      data-testid={`push-failure-${failure.id}`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="font-medium">
            {failure.target}{" "}
            <span
              className="text-xs font-normal text-muted-foreground"
              data-testid="push-failure-status"
            >
              ({failure.status})
            </span>
          </p>
          {diagnosis ? (
            <p
              className="mt-1 text-sm text-muted-foreground"
              data-testid="push-failure-cause"
            >
              {diagnosis.cause} {diagnosis.recommended_action}
            </p>
          ) : failure.error ? (
            <p className="mt-1 text-sm text-muted-foreground">
              {failure.error.message ?? failure.error.code}
            </p>
          ) : null}
          {diagnosis?.needs_reauth ? (
            <p role="alert" className="mt-1 text-sm text-destructive">
              This connection needs to be reconnected before a retry can succeed.
            </p>
          ) : null}
        </div>

        <button
          type="button"
          data-testid={`retry-push-btn-${failure.id}`}
          disabled={retry.isPending || !canRetry}
          title={
            canRetry
              ? "Retry this push"
              : (diagnosis?.recommended_action ?? "Fix the issue before retrying")
          }
          onClick={() => retry.mutate(failure.id)}
          className="shrink-0 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
        >
          {retry.isPending ? "Retrying…" : "Retry"}
        </button>
      </div>

      {retry.isSuccess ? (
        <p role="status" className="mt-2 text-sm text-green-600">
          {retry.data.status === "success"
            ? "Retry succeeded."
            : `Retry attempted — still ${retry.data.status}.`}
        </p>
      ) : null}
      {retry.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          Could not retry the push. Try again.
        </p>
      ) : null}

      <button
        type="button"
        data-testid={`toggle-detail-${failure.id}`}
        onClick={() => setExpanded((v) => !v)}
        className="mt-2 text-xs text-muted-foreground underline"
        aria-expanded={expanded}
      >
        {expanded ? "Hide push-log entry" : "View push-log entry"}
      </button>

      {expanded ? (
        <dl
          className="mt-2 grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs text-muted-foreground"
          data-testid={`push-failure-detail-${failure.id}`}
        >
          <dt>Push-log id</dt>
          <dd className="font-mono break-all">{failure.id}</dd>
          <dt>Connection</dt>
          <dd className="font-mono break-all">{failure.connection_id}</dd>
          {failure.signal_id ? (
            <>
              <dt>Signal</dt>
              <dd className="font-mono break-all">{failure.signal_id}</dd>
            </>
          ) : null}
          {failure.error ? (
            <>
              <dt>Error code</dt>
              <dd>{failure.error.code}</dd>
            </>
          ) : null}
          <dt>Attempts</dt>
          <dd>{failure.attempt_count}</dd>
          <dt>Last attempt</dt>
          <dd>{failure.attempted_at ?? failure.created_at}</dd>
        </dl>
      ) : null}
    </li>
  );
}
