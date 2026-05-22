"use client";

// Billing settings island — /settings/billing (N5).
//
// Shows the current plan, usage meters, and upgrade/downgrade actions.
// Admin-gated on the API side; non-admins who navigate here will see the
// plan info but mutation errors are surfaced inline (the API returns 403).
//
// State model (doc 06 §2):
//   - TanStack Query: server state (plan, usage, limits, mutations).
//   - Zustand/sessionStore: auth token + active workspace id.
//   - Local useState: loading/error UI for mutations only.

import { useState } from "react";
import {
  useWorkspacePlan,
  useWorkspaceUsage,
  useBillingLimits,
  useChangePlan,
  useCreatePortalSession,
  type SubscriptionPlan,
} from "@/hooks/use-billing";
import { useActiveWorkspace, useWorkspaces } from "@/hooks/use-workspaces";
import { ProblemError } from "@/lib/auth-api";

// Plans available for self-serve change; enterprise is contact-driven.
const SELF_SERVE_PLANS: { id: SubscriptionPlan; label: string; price: string }[] = [
  { id: "solo", label: "Solo", price: "$19/seat/mo" },
  { id: "starter", label: "Starter", price: "$49/seat/mo" },
  { id: "pro", label: "Pro", price: "$149/seat/mo" },
];

function planLabel(plan: string): string {
  return plan.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function dimLabel(dim: string): string {
  return dim.replace(/_/g, " ").replace(/per month/i, "/mo");
}

function UsageMeter({
  label,
  used,
  limit,
  pct,
  state,
}: {
  label: string;
  used: number;
  limit: number | null;
  pct: number | null;
  state: string;
}) {
  const barColor =
    state === "exceeded"
      ? "bg-red-500"
      : state === "warning"
        ? "bg-amber-400"
        : "bg-primary";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span className="capitalize text-muted-foreground">{label}</span>
        <span className="font-medium">
          {used.toLocaleString()}
          {limit !== null ? ` / ${limit.toLocaleString()}` : " (unlimited)"}
        </span>
      </div>
      {limit !== null && pct !== null && (
        <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full rounded-full transition-all ${barColor}`}
            style={{ width: `${Math.min(pct, 100)}%` }}
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            role="progressbar"
            aria-label={label}
          />
        </div>
      )}
    </div>
  );
}

export function BillingSettings() {
  const { data: workspaces } = useWorkspaces();
  const active = useActiveWorkspace(workspaces);
  const workspaceId = active?.id ?? null;

  const { data: planInfo, isLoading: planLoading } = useWorkspacePlan(workspaceId);
  const { data: usage } = useWorkspaceUsage(workspaceId);
  const { data: limits } = useBillingLimits(workspaceId);

  const changePlanMutation = useChangePlan(workspaceId);
  const portalSessionMutation = useCreatePortalSession(workspaceId);

  const [changePlanError, setChangePlanError] = useState<string | null>(null);
  const [portalError, setPortalError] = useState<string | null>(null);

  const currentPlan = planInfo?.effective_plan.plan ?? null;

  function handleChangePlan(targetPlan: SubscriptionPlan) {
    setChangePlanError(null);
    changePlanMutation.mutate(targetPlan, {
      onError: (err: unknown) => {
        if (err instanceof ProblemError) {
          setChangePlanError(err.problem.detail ?? "Plan change failed.");
        } else {
          setChangePlanError("An unexpected error occurred. Please try again.");
        }
      },
    });
  }

  function handleOpenPortal() {
    setPortalError(null);
    const returnUrl =
      typeof window !== "undefined" ? window.location.href : "/settings/billing";
    portalSessionMutation.mutate(returnUrl, {
      onSuccess: (result) => {
        window.location.href = result.url;
      },
      onError: (err: unknown) => {
        if (err instanceof ProblemError) {
          setPortalError(err.problem.detail ?? "Could not open billing portal.");
        } else {
          setPortalError("An unexpected error occurred. Please try again.");
        }
      },
    });
  }

  if (!workspaceId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select or create a workspace to manage billing.
      </p>
    );
  }

  if (planLoading) {
    return (
      <p className="text-sm text-muted-foreground" aria-live="polite">
        Loading billing info…
      </p>
    );
  }

  return (
    <div className="space-y-8" data-testid="billing-settings">
      {/* Current plan */}
      <section aria-labelledby="current-plan-heading">
        <h2
          id="current-plan-heading"
          className="mb-3 text-lg font-semibold"
        >
          Current plan
        </h2>
        <div className="rounded-lg border bg-card p-4">
          <p className="text-2xl font-bold">
            {planInfo ? planLabel(planInfo.effective_plan.plan) : "—"}
          </p>
          {planInfo && (
            <p className="mt-1 text-sm text-muted-foreground">
              {planInfo.effective_plan.display_name}
            </p>
          )}
        </div>
      </section>

      {/* Usage meters */}
      {usage && (
        <section aria-labelledby="usage-heading">
          <h2 id="usage-heading" className="mb-3 text-lg font-semibold">
            Usage this period
          </h2>
          <div className="space-y-4 rounded-lg border bg-card p-4">
            {Object.values(usage.dimensions).map((dim) => {
              const limitEntry = limits?.dimensions[dim.dimension];
              return (
                <UsageMeter
                  key={dim.dimension}
                  label={dimLabel(dim.dimension)}
                  used={dim.used}
                  limit={dim.limit}
                  pct={dim.pct_used}
                  state={limitEntry?.state ?? "ok"}
                />
              );
            })}
          </div>
        </section>
      )}

      {/* Plan change */}
      <section aria-labelledby="change-plan-heading">
        <h2 id="change-plan-heading" className="mb-3 text-lg font-semibold">
          Change plan
        </h2>
        <p className="mb-4 text-sm text-muted-foreground">
          Upgrade or downgrade your workspace plan. Stripe calculates proration
          automatically for the current billing period.
        </p>
        {changePlanError && (
          <div
            role="alert"
            className="mb-3 rounded-md border border-destructive bg-destructive/10 p-3 text-sm text-destructive"
          >
            {changePlanError}
          </div>
        )}
        {changePlanMutation.isSuccess && (
          <div
            role="status"
            className="mb-3 rounded-md border border-green-500 bg-green-50 p-3 text-sm text-green-700"
          >
            Plan changed successfully.
          </div>
        )}
        <div
          className="grid gap-3 sm:grid-cols-3"
          aria-label="Available plans"
        >
          {SELF_SERVE_PLANS.map((plan) => {
            const isCurrent = currentPlan === plan.id;
            return (
              <div
                key={plan.id}
                className={`rounded-lg border p-4 ${
                  isCurrent ? "border-primary bg-primary/5" : "bg-card"
                }`}
              >
                <p className="font-semibold">{plan.label}</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  {plan.price}
                </p>
                <button
                  type="button"
                  disabled={
                    isCurrent ||
                    changePlanMutation.isPending ||
                    portalSessionMutation.isPending
                  }
                  onClick={() => handleChangePlan(plan.id)}
                  aria-label={
                    isCurrent ? `Current plan: ${plan.label}` : `Switch to ${plan.label}`
                  }
                  data-testid={`change-plan-${plan.id}`}
                  className={`mt-3 w-full rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    isCurrent
                      ? "cursor-default bg-primary/20 text-primary"
                      : "bg-primary text-primary-foreground hover:bg-primary/90"
                  }`}
                >
                  {isCurrent
                    ? "Current"
                    : changePlanMutation.isPending
                      ? "Changing…"
                      : "Switch"}
                </button>
              </div>
            );
          })}
        </div>
        <p className="mt-3 text-xs text-muted-foreground">
          Enterprise plans are quote-driven.{" "}
          <a
            href="mailto:sales@civicsignals.io"
            className="underline underline-offset-4"
          >
            Contact sales
          </a>
          .
        </p>
      </section>

      {/* Stripe Customer Portal */}
      <section aria-labelledby="portal-heading">
        <h2 id="portal-heading" className="mb-3 text-lg font-semibold">
          Billing portal
        </h2>
        <p className="mb-3 text-sm text-muted-foreground">
          Manage payment methods, download invoices, and access full billing
          history in the Stripe Customer Portal.
        </p>
        {portalError && (
          <div
            role="alert"
            className="mb-3 rounded-md border border-destructive bg-destructive/10 p-3 text-sm text-destructive"
          >
            {portalError}
          </div>
        )}
        <button
          type="button"
          onClick={handleOpenPortal}
          disabled={
            portalSessionMutation.isPending || changePlanMutation.isPending
          }
          data-testid="open-portal-btn"
          className="rounded-md border bg-background px-4 py-2 text-sm font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
        >
          {portalSessionMutation.isPending
            ? "Opening portal…"
            : "Open billing portal"}
        </button>
      </section>
    </div>
  );
}
