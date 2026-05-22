// FoiaList — client island for the /foia list page (M4).
//
// - Status filter via <select>; search by subject via debounced text input.
// - TanStack Query infinite scroll via useFoiaRequests.
// - Renders FoiaRequestCard per result; "Load more" button advances cursor.

"use client";

import { useCallback, useState, useTransition } from "react";
import { useFoiaRequests } from "@/hooks/use-foia";
import { FoiaRequestCard } from "@/components/foia/foia-request-card";
import { ProblemError } from "@/lib/auth-api";
import type { FoiaStatus } from "@/lib/foia-api";

const STATUS_OPTIONS: { value: FoiaStatus | ""; label: string }[] = [
  { value: "", label: "All statuses" },
  { value: "draft", label: "Draft" },
  { value: "sent", label: "Awaiting" },
  { value: "ack", label: "Acknowledged" },
  { value: "response", label: "Responded" },
];

const SEARCH_DEBOUNCE = 300;

export function FoiaList() {
  const [statusFilter, setStatusFilter] = useState<FoiaStatus | "">("");
  const [, startTransition] = useTransition();
  const [debounceTimer, setDebounceTimer] = useState<ReturnType<typeof setTimeout> | null>(null);
  // Note: client-side subject search — the API doesn't support q= on FOIA yet,
  // so we filter the fetched items locally while keeping the list page lean.
  const [searchInput, setSearchInput] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const handleSearchChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const val = e.target.value;
      setSearchInput(val);
      if (debounceTimer) clearTimeout(debounceTimer);
      const timer = setTimeout(() => {
        startTransition(() => setDebouncedSearch(val));
      }, SEARCH_DEBOUNCE);
      setDebounceTimer(timer);
    },
    [debounceTimer],
  );

  const apiFilters = {
    ...(statusFilter ? { status: statusFilter } : {}),
  };

  const {
    data,
    isLoading,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    error,
  } = useFoiaRequests(apiFilters);

  const allItems = data?.pages.flatMap((p) => p.items) ?? [];

  // Client-side subject search filter (case-insensitive substring).
  const filtered = debouncedSearch
    ? allItems.filter((r) =>
        r.subject.toLowerCase().includes(debouncedSearch.toLowerCase()),
      )
    : allItems;

  return (
    <div className="space-y-6">
      {/* Filter bar */}
      <div className="flex flex-wrap gap-3">
        <div className="relative flex-1 min-w-[180px]">
          <label className="sr-only" htmlFor="foia-search">
            Search FOIA requests
          </label>
          <input
            id="foia-search"
            type="search"
            role="searchbox"
            aria-label="Search FOIA requests"
            placeholder="Search by subject…"
            value={searchInput}
            onChange={handleSearchChange}
            className="w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>

        <label className="sr-only" htmlFor="foia-status-filter">
          Filter by status
        </label>
        <select
          id="foia-status-filter"
          aria-label="Filter by status"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as FoiaStatus | "")}
          className="rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {STATUS_OPTIONS.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {/* Count line */}
      {!isLoading && !error && (
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {filtered.length === 0
            ? "No FOIA requests match your filters."
            : `Showing ${filtered.length} ${filtered.length === 1 ? "request" : "requests"}${hasNextPage ? " (more available)" : ""}`}
        </p>
      )}

      {/* Loading skeleton */}
      {isLoading && (
        <ul aria-label="Loading FOIA requests" aria-busy="true" className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <li key={i} className="h-20 animate-pulse rounded-lg border bg-muted" />
          ))}
        </ul>
      )}

      {/* Error state */}
      {error && (
        <div
          role="alert"
          className="rounded-lg border border-destructive/50 bg-destructive/10 px-5 py-4 text-sm text-destructive"
        >
          {error instanceof ProblemError
            ? (error.problem.detail ?? error.problem.title)
            : error.message}
        </div>
      )}

      {/* Results list */}
      {!isLoading && filtered.length > 0 && (
        <ul className="space-y-3" aria-label="FOIA requests">
          {filtered.map((req) => (
            <li key={req.id}>
              <FoiaRequestCard request={req} />
            </li>
          ))}
        </ul>
      )}

      {/* Load more */}
      {!isLoading && hasNextPage && (
        <div className="flex justify-center pt-2">
          <button
            type="button"
            onClick={() => void fetchNextPage()}
            disabled={isFetchingNextPage}
            className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
          >
            {isFetchingNextPage ? "Loading…" : "Load more"}
          </button>
        </div>
      )}

      {/* Empty state — no requests at all */}
      {!isLoading && !error && allItems.length === 0 && !statusFilter && !searchInput && (
        <div className="py-16 text-center text-muted-foreground">
          <p className="text-lg font-medium">No FOIA requests yet.</p>
          <p className="mt-1 text-sm">
            Create your first request using the button above.
          </p>
        </div>
      )}
    </div>
  );
}
