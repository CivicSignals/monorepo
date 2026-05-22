// EntityDirectory — client island for the /entities directory page (C3).
//
// - Client-side filter state (q, type, state, status) via useState.
// - TanStack Query infinite scroll via useEntities (server state).
// - Renders EntityCard for each result; "Load more" button to advance cursor.

"use client";

import { useCallback, useState, useTransition } from "react";
import { useEntities } from "@/hooks/use-entities";
import { EntityCard } from "@/components/entities/entity-card";
import {
  EntityFiltersBar,
  type EntityFiltersState,
} from "@/components/entities/entity-filters-bar";
import { ProblemError } from "@/lib/auth-api";

const EMPTY_FILTERS: EntityFiltersState = {
  q: "",
  type: "",
  state: "",
  status: "",
};

// Debounce delay for the name search input (ms).
const SEARCH_DEBOUNCE = 300;

export function EntityDirectory() {
  const [filters, setFilters] = useState<EntityFiltersState>(EMPTY_FILTERS);
  // Debounced query value — only forward to the API after the user stops typing.
  const [debouncedQ, setDebouncedQ] = useState("");
  const [debounceTimer, setDebounceTimer] = useState<ReturnType<
    typeof setTimeout
  > | null>(null);
  const [, startTransition] = useTransition();

  const handleFiltersChange = useCallback(
    (updated: EntityFiltersState) => {
      setFilters(updated);
      // Debounce only the text search; dropdowns apply immediately.
      if (updated.q !== filters.q) {
        if (debounceTimer) clearTimeout(debounceTimer);
        const timer = setTimeout(() => {
          startTransition(() => setDebouncedQ(updated.q));
        }, SEARCH_DEBOUNCE);
        setDebounceTimer(timer);
      } else {
        startTransition(() => setDebouncedQ(updated.q));
      }
    },
    [filters.q, debounceTimer],
  );

  // Build API filter params from UI state.
  const apiFilters = {
    ...(debouncedQ ? { q: debouncedQ } : {}),
    ...(filters.type ? { type: [filters.type] } : {}),
    ...(filters.state ? { state: [filters.state] } : {}),
    ...(filters.status ? { status: filters.status } : {}),
  };

  const {
    data,
    isLoading,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    error,
  } = useEntities(apiFilters);

  const allItems = data?.pages.flatMap((p) => p.items) ?? [];
  const totalOnPage = allItems.length;

  return (
    <div className="space-y-6">
      <EntityFiltersBar filters={filters} onChange={handleFiltersChange} />

      {/* Status / count line */}
      {!isLoading && !error && (
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {totalOnPage === 0
            ? "No entities match your filters."
            : `Showing ${totalOnPage.toLocaleString("en-US")} ${totalOnPage === 1 ? "entity" : "entities"}${hasNextPage ? " (more available)" : ""}`}
        </p>
      )}

      {/* Loading skeleton */}
      {isLoading && (
        <ul
          aria-label="Loading entities"
          aria-busy="true"
          className="space-y-3"
        >
          {Array.from({ length: 6 }).map((_, i) => (
            <li
              key={i}
              className="h-20 animate-pulse rounded-lg border bg-muted"
            />
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
      {!isLoading && allItems.length > 0 && (
        <ul className="space-y-3" aria-label="Entity directory results">
          {allItems.map((entity) => (
            <li key={entity.id}>
              <EntityCard entity={entity} />
            </li>
          ))}
        </ul>
      )}

      {/* Load more / end of list */}
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

      {/* Empty state — no filters */}
      {!isLoading &&
        allItems.length === 0 &&
        !error &&
        Object.values(filters).every((v) => v === "") && (
          <div className="py-16 text-center text-muted-foreground">
            <p className="text-lg font-medium">No entities yet.</p>
            <p className="mt-1 text-sm">
              The entity directory populates as data is ingested.
            </p>
          </div>
        )}
    </div>
  );
}
