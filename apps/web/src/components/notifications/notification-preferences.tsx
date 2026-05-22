"use client";

import Link from "next/link";
import { ProblemError } from "@/lib/auth-api";
import type { DigestFrequency } from "@/lib/digests-api";
import { useSetDigest, useUserDigests } from "@/hooks/use-digests";

// Per-user notification preferences (H5).
//
// The consolidated /settings/notifications surface: lists the current member's
// digest subscriptions in the active workspace (one row per saved search), and
// lets them change the frequency or unsubscribe per row (per-search, per-user).
// This is the target of the digest-email footer's "Manage your digest
// preferences" link. The per-saved-search frequency control (H3) lives on the
// saved-search rows; this page is the single place to see them all.
//
// Server state is owned by TanStack Query (doc 06 §2); this island holds no
// server data of its own.

const FREQUENCIES: { value: DigestFrequency; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
];

const FREQUENCY_LABEL: Record<DigestFrequency, string> = {
  off: "Off",
  daily: "Daily",
  weekly: "Weekly",
};

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function PreferenceRow({
  savedSearchId,
  savedSearchName,
  frequency,
  sendHour,
  weekday,
  timezone,
}: {
  savedSearchId: string;
  savedSearchName: string;
  frequency: DigestFrequency;
  sendHour: number;
  weekday: number;
  timezone: string;
}) {
  const mutation = useSetDigest(savedSearchId);

  const onChange = (next: DigestFrequency) => {
    // Preserve the existing schedule fields; only the frequency changes here.
    mutation.mutate({
      frequency: next,
      send_hour: sendHour,
      weekday,
      timezone: timezone || browserTimezone(),
    });
  };

  const error =
    mutation.error instanceof ProblemError
      ? mutation.error.problem.detail
      : mutation.error?.message;

  const selectId = `digest-frequency-${savedSearchId}`;
  // Optimistic display value while a mutation is in flight.
  const shown = mutation.isPending
    ? (mutation.variables?.frequency ?? frequency)
    : frequency;

  return (
    <li
      data-testid={`digest-pref-${savedSearchId}`}
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4"
    >
      <div className="min-w-0">
        <p className="truncate font-medium">{savedSearchName}</p>
        <p className="text-xs text-muted-foreground">
          Currently: {FREQUENCY_LABEL[shown]}
          {shown !== "off" ? ` · ${timezone}` : ""}
        </p>
        {error ? (
          <p role="alert" className="mt-1 text-xs text-destructive">
            {error}
          </p>
        ) : null}
      </div>
      <div className="flex items-center gap-2">
        <label htmlFor={selectId} className="text-xs text-muted-foreground">
          Digest
        </label>
        <select
          id={selectId}
          aria-label={`Digest frequency for ${savedSearchName}`}
          value={shown}
          disabled={mutation.isPending}
          onChange={(e) => onChange(e.target.value as DigestFrequency)}
          className="rounded-md border bg-background px-2 py-1 text-sm disabled:opacity-60"
        >
          {FREQUENCIES.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </select>
        {shown !== "off" ? (
          <button
            type="button"
            onClick={() => onChange("off")}
            disabled={mutation.isPending}
            className="rounded-md border px-2 py-1 text-sm text-muted-foreground hover:bg-muted disabled:opacity-60"
          >
            Unsubscribe
          </button>
        ) : null}
      </div>
    </li>
  );
}

export function NotificationPreferences() {
  const query = useUserDigests();

  if (query.isLoading) {
    return (
      <p data-testid="digest-prefs-loading" className="text-muted-foreground">
        Loading your notification preferences…
      </p>
    );
  }

  const error =
    query.error instanceof ProblemError
      ? query.error.problem.detail
      : query.error?.message;
  if (error) {
    return (
      <p role="alert" className="text-destructive">
        {error}
      </p>
    );
  }

  const subscriptions = query.data ?? [];

  return (
    <div data-testid="notification-preferences">
      {subscriptions.length === 0 ? (
        <p className="text-muted-foreground">
          You have no digest subscriptions yet. Turn on a digest from a{" "}
          <Link
            href="/settings/saved-searches"
            className="text-primary underline"
          >
            saved search
          </Link>{" "}
          to get periodic email updates.
        </p>
      ) : (
        <ul data-testid="digest-prefs-list" className="space-y-3">
          {subscriptions.map((sub) => (
            <PreferenceRow
              key={sub.id}
              savedSearchId={sub.saved_search_id}
              savedSearchName={sub.saved_search_name}
              frequency={sub.frequency}
              sendHour={sub.send_hour}
              weekday={sub.weekday}
              timezone={sub.timezone}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
