// Reusable empty / error / skeleton state primitives (G5).
//
// A small, consistent set of UI building blocks for the three (well, four)
// non-happy-path states the signal surfaces need to render: loading skeletons,
// "nothing here" empty states, "it broke" error states with a retry affordance,
// and a disabled / sign-in-required state. These are intentionally generic so
// the feed, the signal detail page, and future surfaces can share them rather
// than each hand-rolling its own copy (the G5 brief asks for consistency).
//
// Server state still lives in TanStack Query (doc 06 §2); these are pure,
// stateless presentational components driven entirely by props.

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

// ---- Skeleton primitive -----------------------------------------------------

/**
 * A single shimmering placeholder block. Compose these to mirror a real
 * layout so the swap from skeleton → content does not shift the page.
 */
export function SkeletonBlock({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded bg-muted", className)}
    />
  );
}

// ---- EmptyState -------------------------------------------------------------

export interface EmptyStateProps {
  /** Short, bold headline (e.g. "No signals yet"). */
  title: string;
  /** Optional supporting copy under the title. */
  description?: ReactNode;
  /** Optional decorative glyph / icon shown above the title. */
  icon?: ReactNode;
  /** Optional call-to-action (button or link) shown below the copy. */
  action?: ReactNode;
  /** data-testid for the wrapper so callers can assert the specific empty case. */
  testId?: string;
  className?: string;
}

/**
 * A centred, illustrated empty state. Use for "there is genuinely nothing here"
 * as well as "your filters matched nothing" (vary the copy + action per case).
 */
export function EmptyState({
  title,
  description,
  icon,
  action,
  testId,
  className,
}: EmptyStateProps) {
  return (
    <div
      data-testid={testId}
      className={cn(
        "flex flex-col items-center justify-center py-16 px-4 text-center",
        className,
      )}
    >
      {icon && <div className="mb-3 text-muted-foreground/60">{icon}</div>}
      <p className="text-base font-semibold text-foreground">{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

// ---- ErrorState -------------------------------------------------------------

export interface ErrorStateProps {
  /** Short headline; defaults to a generic failure message. */
  title?: string;
  /** The specific error detail (e.g. the caught error's message). */
  message?: string;
  /** Wire to TanStack Query's `refetch`. Renders a "Try again" button when set. */
  onRetry?: () => void;
  /** Disable the retry button while a retry is in flight. */
  retrying?: boolean;
  /** data-testid for the wrapper. */
  testId?: string;
  className?: string;
}

/**
 * A clear, accessible error state with an optional retry affordance. Rendered
 * as `role="alert"` so assistive tech announces it.
 */
export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  retrying = false,
  testId,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      data-testid={testId}
      className={cn(
        "rounded-lg border border-destructive/50 bg-destructive/10 px-5 py-4 text-sm",
        className,
      )}
    >
      <p className="font-semibold text-destructive">{title}</p>
      {message && <p className="mt-1 text-destructive/90">{message}</p>}
      {onRetry && (
        <button
          type="button"
          data-testid="error-retry"
          onClick={onRetry}
          disabled={retrying}
          className="mt-3 inline-flex items-center rounded-md border border-destructive/40 bg-background px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/10 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {retrying ? "Retrying…" : "Try again"}
        </button>
      )}
    </div>
  );
}

// ---- AuthRequiredState ------------------------------------------------------

export interface AuthRequiredStateProps {
  /** Where the sign-in CTA points. Defaults to /login. */
  href?: string;
  /** Copy override. */
  title?: string;
  description?: ReactNode;
  testId?: string;
  className?: string;
}

/**
 * Shown when a query is *disabled* because there is no auth token / active
 * workspace yet. This is distinct from "not found": the request was never made,
 * so we must not imply the resource is missing (fixes the G2 fall-through where a
 * disabled detail query rendered "Signal not found").
 */
export function AuthRequiredState({
  href = "/login",
  title = "Sign in to continue",
  description = "Select a workspace or sign in to view this content.",
  testId,
  className,
}: AuthRequiredStateProps) {
  return (
    <div
      data-testid={testId}
      role="status"
      className={cn(
        "flex flex-col items-center justify-center py-16 px-4 text-center text-muted-foreground",
        className,
      )}
    >
      <p className="text-base font-semibold text-foreground">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm">{description}</p>}
      <a
        href={href}
        className="mt-4 inline-block text-sm text-primary underline-offset-2 hover:underline"
      >
        Go to sign in &rarr;
      </a>
    </div>
  );
}
