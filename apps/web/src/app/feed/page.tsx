// /feed — workspace signal feed (G1).
//
// The keystone product surface: a workspace's per-workspace-scored signals,
// sorted by score desc, with filter controls wired to URL search params so
// filters survive a page refresh and are shareable via link.
//
// State model (doc 06 §2):
// - TanStack Query owns server state (feed data via useWorkspaceFeed).
// - URL search params carry filter state (ephemeral UI, not Zustand).
// - activeWorkspaceId in Zustand UI store selects which workspace to load for.
//
// Seams:
// - G2 (done): feed rows link through to the signal detail page at /signals/[id].
// - G3 (done): bulk actions (mass dismiss / pin) — multi-select + BulkActionBar live
//   inside FeedList.
// - G4 (done): per-signal status transitions (StatusControls on each feed row).
// - G5 (done): polished loading skeleton / empty / error / auth-required states
//   live inside FeedList (see components/ui/states.tsx for the shared primitives).

"use client";

import { Suspense, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { FeedList, type FeedFiltersState } from "@/components/signals/feed-list";
import type { SignalType, FeedStatus } from "@/lib/signals-api";

// ---- Helpers ----------------------------------------------------------------

function parseFeedFilters(params: URLSearchParams): FeedFiltersState {
  const signal_type = params.get("signal_type") as SignalType | null;
  const status = params.get("status") as FeedStatus | null;
  const min_score_raw = params.get("min_score");
  const min_score = min_score_raw ? Number(min_score_raw) : undefined;
  return {
    ...(signal_type ? { signal_type } : {}),
    ...(status ? { status } : {}),
    ...(min_score !== undefined && !isNaN(min_score) ? { min_score } : {}),
  };
}

// ---- Inner component (reads useSearchParams — must be inside Suspense) ------

function FeedPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const filters = parseFeedFilters(searchParams);

  const handleFiltersChange = useCallback(
    (next: FeedFiltersState) => {
      const params = new URLSearchParams();
      if (next.signal_type) params.set("signal_type", next.signal_type);
      if (next.status) params.set("status", next.status);
      if (next.min_score !== undefined) params.set("min_score", String(next.min_score));
      // Replace rather than push so filter changes don't stack in browser history.
      router.replace(`/feed?${params.toString()}`);
    },
    [router],
  );

  return (
    <>
      {/* G3: the bulk-action bar lives inside FeedList (it owns the multi-select state). */}
      {/* G5: FeedList renders the polished loading / empty / error / auth-required states. */}
      <FeedList filters={filters} onFiltersChange={handleFiltersChange} />
    </>
  );
}

// ---- Page -------------------------------------------------------------------

export default function FeedPage() {
  return (
    <main className="container max-w-4xl py-8">
      <div className="mb-6 space-y-1">
        <h1 className="text-3xl font-bold tracking-tight">Signal Feed</h1>
        <p className="text-muted-foreground text-sm">
          Signals matching your workspace ICP, scored and ranked by relevance.
        </p>
      </div>

      {/* Suspense required for useSearchParams() in Next.js 15 App Router. */}
      <Suspense fallback={<div className="py-12 text-center text-sm text-gray-400">Loading...</div>}>
        <FeedPageInner />
      </Suspense>
    </main>
  );
}
