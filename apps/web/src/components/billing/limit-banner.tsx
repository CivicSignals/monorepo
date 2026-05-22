"use client";

// LimitBanner — soft-limit banner and paywall CTA (N4).
//
// Shown in the app shell when any metering dimension reaches ≥ 80% (warning)
// or ≥ 100% (exceeded). Driven by useBillingLimits via TanStack Query.
//
// - warning  (80–99%) → amber banner with "You're approaching a plan limit" + upgrade link.
// - exceeded (≥ 100%) → red banner with "Upgrade required" paywall CTA.
//
// The banner is invisible when all dimensions are "ok" or limits haven't loaded.

import Link from "next/link";
import {
  useBillingLimits,
  worstLimitState,
  alertingDimensions,
  type LimitState,
} from "@/hooks/use-billing";

interface LimitBannerProps {
  /** The active workspace id. Pass null/undefined to suppress the banner. */
  workspaceId: string | null | undefined;
}

function dimLabel(dimension: string): string {
  return dimension.replace(/_/g, " ").replace(/per month/i, "/mo");
}

export function LimitBanner({ workspaceId }: LimitBannerProps) {
  const { data: limits } = useBillingLimits(workspaceId);
  const worst: LimitState = worstLimitState(limits);

  if (worst === "ok") return null;

  const alerting = alertingDimensions(limits);
  const isExceeded = worst === "exceeded";

  const bgClass = isExceeded
    ? "bg-red-600 text-white"
    : "bg-amber-500 text-white";

  const headline = isExceeded
    ? "Plan limit reached — some actions are blocked."
    : "You are approaching a plan limit.";

  const ctaLabel = isExceeded ? "Upgrade now" : "View plans";

  return (
    <div
      role="alert"
      aria-live="polite"
      data-testid="limit-banner"
      data-state={worst}
      className={`flex items-center justify-between gap-4 px-4 py-2 text-sm font-medium ${bgClass}`}
    >
      <span>
        {headline}{" "}
        {alerting.map((d) => (
          <span key={d.dimension} className="mr-2 opacity-90">
            {dimLabel(d.dimension)}: {d.pct !== null ? `${d.pct}%` : "exceeded"}
          </span>
        ))}
      </span>
      <Link
        href="/pricing"
        className="shrink-0 rounded bg-white/20 px-3 py-1 text-xs font-semibold hover:bg-white/30 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
      >
        {ctaLabel}
      </Link>
    </div>
  );
}
