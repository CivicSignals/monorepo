"use client";

import { useEffect, useState } from "react";
import { ProblemError } from "@/lib/auth-api";
import type { DigestFrequency } from "@/lib/digests-api";
import { useDigest, useSetDigest } from "@/hooks/use-digests";

// Digest frequency selector for one saved search (H3).
//
// A small inline control on a saved-search row: pick off / daily / weekly. The
// recipient timezone defaults to the browser's IANA zone (Intl) so a digest sends
// at the user's local send-hour without a separate tz picker. Changing the value
// PUTs the new schedule through TanStack Query.
//
// TODO H5: a fuller preferences surface (send-hour, weekday, explicit timezone,
//   one-click unsubscribe) replaces this minimal selector.

const FREQUENCIES: { value: DigestFrequency; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "daily", label: "Daily" },
  { value: "weekly", label: "Weekly" },
];

function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function DigestFrequencySelect({
  savedSearchId,
}: {
  savedSearchId: string;
}) {
  const query = useDigest(savedSearchId);
  const mutation = useSetDigest(savedSearchId);

  // Local mirror so the <select> reflects the user's choice immediately while the
  // PUT is in flight (TanStack Query is still the source of truth on settle).
  const [value, setValue] = useState<DigestFrequency>("off");
  useEffect(() => {
    setValue(query.data?.frequency ?? "off");
  }, [query.data?.frequency]);

  const onChange = (next: DigestFrequency) => {
    setValue(next);
    mutation.mutate({ frequency: next, timezone: browserTimezone() });
  };

  const error =
    mutation.error instanceof ProblemError
      ? mutation.error.problem.detail
      : mutation.error?.message;

  const selectId = `digest-frequency-${savedSearchId}`;

  return (
    <div className="flex items-center gap-2">
      <label htmlFor={selectId} className="text-xs text-muted-foreground">
        Digest
      </label>
      <select
        id={selectId}
        aria-label="Digest frequency"
        value={value}
        disabled={query.isLoading || mutation.isPending}
        onChange={(e) => onChange(e.target.value as DigestFrequency)}
        className="rounded-md border bg-background px-2 py-1 text-xs disabled:opacity-60"
      >
        {FREQUENCIES.map((f) => (
          <option key={f.value} value={f.value}>
            {f.label}
          </option>
        ))}
      </select>
      {error ? (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      ) : null}
    </div>
  );
}
