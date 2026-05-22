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
// Seams:
// - TODO G2: click-through to /signals/[id] detail page.
// - TODO G5: polish loading skeletons / empty / error states.

"use client";

import type { FeedItemRead, FeedFilters, SignalType, FeedStatus } from "@/lib/signals-api";
import { SIGNAL_TYPE_LABELS } from "@/lib/signals-api";
import { useWorkspaceFeed } from "@/hooks/use-signals";

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

// ---- Sub-components ---------------------------------------------------------

function FeedItemCard({ item }: { item: FeedItemRead }) {
  const { label: bandLabel, className: bandClass } = scoreBand(item.score);
  const sig = item.signal;

  return (
    // TODO G2: replace with <Link href={`/signals/${sig.id}`}> once detail page lands.
    <article
      data-testid="feed-item"
      className="border border-gray-200 rounded-lg p-4 hover:border-gray-300 hover:shadow-sm transition-all bg-white"
      aria-label={sig.title}
    >
      <div className="flex items-start justify-between gap-4">
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
          {/* Title: link to detail placeholder (G2) */}
          <h3 className="text-sm font-semibold text-gray-900 line-clamp-2 mb-1">
            {/* TODO G2: wrap in <Link href={`/signals/${sig.id}`}> */}
            {sig.title}
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
    </article>
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
 * TODO G5: replace basic loading/empty/error with polished skeleton/empty-state.
 */
export function FeedList({ filters = {}, onFiltersChange }: FeedListProps) {
  // Map FeedFiltersState → FeedFilters for the hook.
  const queryFilters: Omit<FeedFilters, "cursor"> = {
    ...(filters.signal_type ? { signal_type: filters.signal_type } : {}),
    ...(filters.status ? { status: [filters.status] } : {}),
    ...(filters.min_score !== undefined ? { min_score: filters.min_score } : {}),
  };

  const { data, isLoading, isError, error, fetchNextPage, hasNextPage, isFetchingNextPage } =
    useWorkspaceFeed(queryFilters);

  const allItems = data?.pages.flatMap((p) => p.data) ?? [];

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      {onFiltersChange && (
        <FilterBar filters={filters} onChange={onFiltersChange} />
      )}

      {/* Loading state — TODO G5: replace with skeleton */}
      {isLoading && (
        <div
          data-testid="feed-loading"
          className="py-12 text-center text-sm text-gray-400"
          role="status"
          aria-live="polite"
        >
          Loading signals...
        </div>
      )}

      {/* Error state — TODO G5: replace with error card */}
      {isError && (
        <div
          data-testid="feed-error"
          className="py-8 text-center text-sm text-red-500"
          role="alert"
        >
          Failed to load signals: {error?.message ?? "Unknown error"}
        </div>
      )}

      {/* Empty state — TODO G5: replace with illustrated empty-state */}
      {!isLoading && !isError && allItems.length === 0 && (
        <div
          data-testid="feed-empty"
          className="py-16 text-center text-sm text-gray-400"
        >
          <p className="font-medium text-gray-500 mb-1">No signals yet</p>
          <p>
            Signals matching your ICP will appear here once the scoring pipeline
            has run.
          </p>
        </div>
      )}

      {/* Feed items */}
      {allItems.length > 0 && (
        <ul className="space-y-3" data-testid="feed-list" aria-label="Signal feed">
          {allItems.map((item) => (
            <li key={item.score_id}>
              <FeedItemCard item={item} />
            </li>
          ))}
        </ul>
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
