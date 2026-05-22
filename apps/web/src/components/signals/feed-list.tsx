// FeedList — workspace signal feed UI (G1).
//
// Renders the workspace's scored signals as a filterable, score-ranked list.
// Each row shows: title, type badge, score/band, entity, date, source link.
// Filter controls (type, status, min score) are wired to query params via the
// parent page; cursor "load more" appends the next page via TanStack Query.
//
// State model (doc 06 §2):
// - TanStack Query owns server state (feed data).
// - Filters are lifted to the parent via props so the page can encode them in
//   URL search params without a Zustand store (they are ephemeral UI state,
//   not workspace-level client state).
//
// G5: polished loading skeleton (mirrors a feed row), distinct empty states
// (no filters → "no signals yet"; active filters → "no matches, clear filters"),
// an error state with a retry affordance, and an auth-required state for the
// disabled query (no token / workspace) so we never imply the feed is empty.

"use client";

import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import type {
  FeedItemRead,
  FeedFilters,
  ScoreBreakdown,
  SignalType,
  FeedStatus,
} from "@/lib/signals-api";
import { SIGNAL_TYPE_LABELS } from "@/lib/signals-api";
import { useWorkspaceFeed } from "@/hooks/use-signals";
import { StatusControls } from "@/components/signals/status-controls";
import { BulkActionBar } from "@/components/signals/bulk-action-bar";
import {
  AuthRequiredState,
  EmptyState,
  ErrorState,
  SkeletonBlock,
} from "@/components/ui/states";

// ---- Helpers ----------------------------------------------------------------

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function scoreBand(score: number): { label: string; className: string } {
  if (score >= 80)
    return { label: "High", className: "bg-green-100 text-green-800" };
  if (score >= 50)
    return { label: "Medium", className: "bg-yellow-100 text-yellow-800" };
  return { label: "Low", className: "bg-gray-100 text-gray-600" };
}

function signalTypeLabel(type: string): string {
  return SIGNAL_TYPE_LABELS[type as SignalType] ?? type;
}

/**
 * The single top "why this signal?" bullet for a compact feed-row hint (F4).
 * Returns the first scorer-seeded bullet, sentence-cased; null when the breakdown
 * carries none (the full explanation lives on the detail page's WhyThisSignal panel).
 */
function topWhyBullet(breakdown: Record<string, unknown>): string | null {
  const bullets = (breakdown as ScoreBreakdown).bullets;
  const first = bullets?.find((b) => b.trim().length > 0)?.trim();
  if (!first) return null;
  return first.charAt(0).toUpperCase() + first.slice(1);
}

// ---- Sub-components ---------------------------------------------------------

function FeedItemCard({
  item,
  selected,
  onToggleSelect,
}: {
  item: FeedItemRead;
  selected: boolean;
  onToggleSelect: (signalId: string) => void;
}) {
  const { label: bandLabel, className: bandClass } = scoreBand(item.score);
  const sig = item.signal;
  const why = topWhyBullet(item.score_breakdown);

  return (
    <article
      data-testid="feed-item"
      data-selected={selected ? "true" : "false"}
      className={`border rounded-lg p-4 hover:border-gray-300 hover:shadow-sm transition-all bg-white ${
        selected ? "border-indigo-300 ring-1 ring-indigo-200" : "border-gray-200"
      }`}
      aria-label={sig.title}
    >
      <div className="flex items-start gap-3">
        {/* Multi-select checkbox (G3) */}
        <input
          type="checkbox"
          data-testid="feed-item-select"
          aria-label={`Select ${sig.title}`}
          checked={selected}
          onChange={() => onToggleSelect(sig.id)}
          className="mt-1 h-4 w-4 flex-shrink-0 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
        />
        <div className="flex flex-1 items-start justify-between gap-4 min-w-0">
        <div className="flex-1 min-w-0">
          {/* Title + type */}
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="text-xs font-medium text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full">
              {signalTypeLabel(sig.signal_type)}
            </span>
            {/* Feed status badge — visible for non-new statuses */}
            {item.status !== "new" && (
              <span className="text-xs text-gray-500 capitalize">{item.status}</span>
            )}
          </div>
          {/* Title links to the signal detail page (G2). */}
          <h3 className="text-sm font-semibold text-gray-900 line-clamp-2 mb-1">
            <Link
              href={`/signals/${sig.id}`}
              data-testid="feed-item-link"
              className="hover:text-indigo-700 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-500"
            >
              {sig.title}
            </Link>
          </h3>
          {/* Summary */}
          <p className="text-xs text-gray-500 line-clamp-2 mb-2">{sig.summary}</p>
          {/* Entity + date row */}
          <div className="flex items-center gap-3 text-xs text-gray-400 flex-wrap">
            {sig.entity_name_raw && (
              <span data-testid="feed-item-entity">{sig.entity_name_raw}</span>
            )}
            <span data-testid="feed-item-date">{formatDate(sig.occurred_at)}</span>
            {/* Source link from details.submission_url (typed as unknown, guard with string cast) */}
            {typeof sig.details?.submission_url === "string" && (
              <a
                href={sig.details.submission_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-indigo-500 hover:underline"
                data-testid="feed-item-source-link"
              >
                Source
              </a>
            )}
          </div>
          {/* Compact "why this signal?" hint — the top scorer bullet (F4). The full
              explanation lives on the detail page's WhyThisSignal panel. */}
          {why && (
            <p
              data-testid="feed-item-why"
              className="mt-2 text-[11px] text-gray-500 line-clamp-1"
              title={why}
            >
              <span className="font-medium text-gray-600">Why:</span> {why}
            </p>
          )}
          {/* Status transition controls (G4) — mark reviewed / pin / dismiss. */}
          <div className="mt-2.5">
            <StatusControls signalId={sig.id} status={item.status} size="sm" />
          </div>
        </div>
        {/* Score badge */}
        <div className="flex-shrink-0 text-right">
          <div
            data-testid="feed-item-score"
            className={`text-xs font-semibold px-2 py-0.5 rounded-full ${bandClass}`}
          >
            {bandLabel}
          </div>
          <div className="text-[11px] text-gray-400 mt-0.5 tabular-nums">
            {item.score.toFixed(0)}/100
          </div>
        </div>
        </div>
      </div>
    </article>
  );
}

/**
 * A single skeleton feed row that mirrors {@link FeedItemCard}'s layout
 * (checkbox · type badge + title + summary + meta on the left, score on the
 * right) so the skeleton → content swap doesn't shift the page.
 */
function FeedItemSkeleton() {
  return (
    <div
      data-testid="feed-item-skeleton"
      className="rounded-lg border border-gray-200 bg-white p-4"
    >
      <div className="flex items-start gap-3">
        <SkeletonBlock className="mt-1 h-4 w-4 flex-shrink-0" />
        <div className="flex flex-1 items-start justify-between gap-4">
          <div className="min-w-0 flex-1 space-y-2">
            <SkeletonBlock className="h-3 w-20 rounded-full" />
            <SkeletonBlock className="h-4 w-3/4" />
            <SkeletonBlock className="h-3 w-full" />
            <SkeletonBlock className="h-3 w-2/5" />
          </div>
          <div className="flex-shrink-0 space-y-1 text-right">
            <SkeletonBlock className="h-4 w-12 rounded-full" />
            <SkeletonBlock className="ml-auto h-3 w-10" />
          </div>
        </div>
      </div>
    </div>
  );
}

/** A list of {@link FeedItemSkeleton} rows shown while the first page loads. */
function FeedListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <ul
      data-testid="feed-loading"
      role="status"
      aria-busy="true"
      aria-label="Loading signals"
      className="space-y-3"
    >
      {Array.from({ length: rows }).map((_, i) => (
        <li key={i}>
          <FeedItemSkeleton />
        </li>
      ))}
    </ul>
  );
}

// ---- Filter controls --------------------------------------------------------

const SIGNAL_TYPES: SignalType[] = [
  "rfp_posted",
  "rfi_rfq",
  "contract_expiring",
  "contract_awarded",
  "budget_approved",
  "grant_awarded",
  "grant_opportunity",
  "leadership_change",
  "board_agenda_item",
  "strategic_plan_published",
  "open_job",
  "news_mention",
];

const FEED_STATUSES: FeedStatus[] = ["new", "reviewed", "pinned", "pushed", "dismissed"];

export interface FeedFiltersState {
  signal_type?: SignalType;
  status?: FeedStatus;
  min_score?: number;
}

function FilterBar({
  filters,
  onChange,
}: {
  filters: FeedFiltersState;
  onChange: (f: FeedFiltersState) => void;
}) {
  return (
    <div
      className="flex items-center gap-3 flex-wrap"
      role="group"
      aria-label="Feed filters"
    >
      {/* Signal type filter */}
      <select
        aria-label="Signal type"
        data-testid="filter-signal-type"
        className="text-sm border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        value={filters.signal_type ?? ""}
        onChange={(e) =>
          onChange({
            ...filters,
            signal_type: (e.target.value || undefined) as SignalType | undefined,
          })
        }
      >
        <option value="">All types</option>
        {SIGNAL_TYPES.map((t) => (
          <option key={t} value={t}>
            {SIGNAL_TYPE_LABELS[t]}
          </option>
        ))}
      </select>

      {/* Status filter */}
      <select
        aria-label="Status"
        data-testid="filter-status"
        className="text-sm border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        value={filters.status ?? ""}
        onChange={(e) =>
          onChange({
            ...filters,
            status: (e.target.value || undefined) as FeedStatus | undefined,
          })
        }
      >
        <option value="">All statuses</option>
        {FEED_STATUSES.map((s) => (
          <option key={s} value={s} className="capitalize">
            {s.charAt(0).toUpperCase() + s.slice(1)}
          </option>
        ))}
      </select>

      {/* Min score filter */}
      <select
        aria-label="Minimum score"
        data-testid="filter-min-score"
        className="text-sm border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
        value={filters.min_score ?? ""}
        onChange={(e) =>
          onChange({
            ...filters,
            min_score: e.target.value ? Number(e.target.value) : undefined,
          })
        }
      >
        <option value="">Any score</option>
        <option value="80">High (80+)</option>
        <option value="50">Medium (50+)</option>
        <option value="30">Low (30+)</option>
      </select>
    </div>
  );
}

// ---- Main FeedList component ------------------------------------------------

export interface FeedListProps {
  filters?: FeedFiltersState;
  onFiltersChange?: (f: FeedFiltersState) => void;
}

/**
 * FeedList — the main workspace signal feed (G1).
 *
 * Renders the scored, filterable signal list. Accepts filter state from the
 * parent (page encodes them in URL search params). TanStack Query drives all
 * data fetching and pagination — server state never touches Zustand (doc 06 §2).
 *
 * G5: polished skeleton (mirrors a row), filter-aware empty state, error state
 * with retry, and an auth-required state for the disabled query.
 */
export function FeedList({ filters = {}, onFiltersChange }: FeedListProps) {
  // Map FeedFiltersState → FeedFilters for the hook.
  const queryFilters: Omit<FeedFilters, "cursor"> = {
    ...(filters.signal_type ? { signal_type: filters.signal_type } : {}),
    ...(filters.status ? { status: [filters.status] } : {}),
    ...(filters.min_score !== undefined ? { min_score: filters.min_score } : {}),
  };

  const hasActiveFilters =
    filters.signal_type !== undefined ||
    filters.status !== undefined ||
    filters.min_score !== undefined;

  const {
    data,
    isLoading,
    isPending,
    isError,
    error,
    fetchStatus,
    refetch,
    isFetching,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useWorkspaceFeed(queryFilters);

  // The query is *disabled* (no token / active workspace) when it is still
  // pending yet not fetching — TanStack v5 reports status:'pending' +
  // fetchStatus:'idle' (so `isLoading`, which is pending && fetching, is false).
  // Surface a sign-in prompt instead of an empty feed so we never imply the
  // workspace has no signals when we simply haven't asked.
  const isDisabled = isPending && fetchStatus === "idle";

  const allItems = useMemo(
    () => data?.pages.flatMap((p) => p.data) ?? [],
    [data],
  );

  // ---- Multi-select state (G3) ----------------------------------------------
  // Ephemeral, view-local client state (not workspace-level), so it lives in React
  // state here rather than Zustand (doc 06 §2). Keyed by signal id (stable across the
  // feed refetch the bulk mutation triggers on settle).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const visibleIds = useMemo(
    () => allItems.map((item) => item.signal.id),
    [allItems],
  );
  const selectedList = useMemo(
    // Only count ids that are still visible — a refetch may have dropped some rows
    // (e.g. dismissed signals leave the default visible set) out from under us.
    () => visibleIds.filter((id) => selectedIds.has(id)),
    [visibleIds, selectedIds],
  );
  const allVisibleSelected =
    visibleIds.length > 0 && selectedList.length === visibleIds.length;

  const toggleSelect = useCallback((signalId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(signalId)) next.delete(signalId);
      else next.add(signalId);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      // If every visible row is already selected, clear; otherwise select all visible.
      const everySelected =
        visibleIds.length > 0 && visibleIds.every((id) => prev.has(id));
      return everySelected ? new Set() : new Set(visibleIds);
    });
  }, [visibleIds]);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      {onFiltersChange && (
        <FilterBar filters={filters} onChange={onFiltersChange} />
      )}

      {/* Bulk-action bar (G3) — appears when ≥ 1 row is selected. */}
      <BulkActionBar selectedIds={selectedList} onClear={clearSelection} />

      {/* Auth-required state — the query is disabled (no token / workspace). Show
          a sign-in prompt rather than an empty/loading feed. */}
      {isDisabled && (
        <AuthRequiredState
          testId="feed-auth-required"
          title="Sign in to see your feed"
          description="Sign in and pick a workspace to view its scored signal feed."
        />
      )}

      {/* Loading skeleton — mirrors a feed row so there's no layout shift. */}
      {!isDisabled && isLoading && <FeedListSkeleton />}

      {/* Error state — with a retry affordance wired to TanStack Query's refetch. */}
      {!isDisabled && isError && (
        <ErrorState
          testId="feed-error"
          title="Couldn't load signals"
          message={error?.message ?? "Unknown error"}
          onRetry={() => void refetch()}
          retrying={isFetching}
        />
      )}

      {/* Empty states — distinguish "filters matched nothing" from "no signals
          at all" (different copy + action). */}
      {!isDisabled && !isLoading && !isError && allItems.length === 0 && (
        hasActiveFilters ? (
          <EmptyState
            testId="feed-empty-filtered"
            title="No signals match these filters"
            description="Try widening or clearing your filters to see more signals."
            action={
              onFiltersChange && (
                <button
                  type="button"
                  data-testid="feed-clear-filters"
                  onClick={() => onFiltersChange({})}
                  className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                >
                  Clear filters
                </button>
              )
            }
          />
        ) : (
          <EmptyState
            testId="feed-empty"
            title="No signals yet"
            description="Signals matching your ICP will appear here once the scoring pipeline has run."
          />
        )
      )}

      {/* Feed items */}
      {allItems.length > 0 && (
        <>
          {/* Select-all-visible header (G3) */}
          <div className="flex items-center gap-2 px-1">
            <input
              type="checkbox"
              data-testid="feed-select-all"
              aria-label="Select all visible signals"
              checked={allVisibleSelected}
              onChange={toggleSelectAll}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            <label className="text-xs text-gray-500">
              {selectedList.length > 0
                ? `${selectedList.length} selected`
                : "Select all"}
            </label>
          </div>

          <ul className="space-y-3" data-testid="feed-list" aria-label="Signal feed">
            {allItems.map((item) => (
              <li key={item.score_id}>
                <FeedItemCard
                  item={item}
                  selected={selectedIds.has(item.signal.id)}
                  onToggleSelect={toggleSelect}
                />
              </li>
            ))}
          </ul>
        </>
      )}

      {/* Load more */}
      {hasNextPage && (
        <div className="text-center pt-2">
          <button
            type="button"
            data-testid="feed-load-more"
            onClick={() => void fetchNextPage()}
            disabled={isFetchingNextPage}
            className="text-sm text-indigo-600 hover:text-indigo-800 font-medium disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isFetchingNextPage ? "Loading..." : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}
