// @vitest-environment jsdom
// N4 — LimitBanner: soft-limit banner and paywall CTA.
//
// Tests the helper logic (worstLimitState, alertingDimensions) and the
// LimitBanner rendering in isolation. The banner renders based on what
// useBillingLimits returns; we test the helpers independently here and
// test the component with fetch mocks.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { WorkspaceLimits } from "@/lib/billing-api";
import { worstLimitState, alertingDimensions } from "@/hooks/use-billing";
import { useSessionStore } from "@/store/session";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeLimits(
  state: "ok" | "warning" | "exceeded",
  pct: number | null = null,
): WorkspaceLimits {
  return {
    workspace_id: "ws-001",
    period: "2026-05-01",
    dimensions: {
      ai_runs_per_month: {
        dimension: "ai_runs_per_month",
        used: pct !== null ? pct : 0,
        limit: 500,
        pct,
        state,
      },
    },
  };
}

// Build a fetch mock that returns the given limits JSON from /billing/limits.
function mockFetchWithLimits(limits: WorkspaceLimits) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(limits), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

// Build a fetch mock that always returns 200 for anything (used for null tests).
function mockFetchEmpty() {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        workspace_id: "ws-001",
        period: "2026-05-01",
        dimensions: {
          ai_runs_per_month: {
            dimension: "ai_runs_per_month",
            used: 0,
            limit: 500,
            pct: 0,
            state: "ok",
          },
        },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSessionStore.setState({ accessToken: null, user: null });
});

// ---------------------------------------------------------------------------
// LimitBanner rendering tests
// ---------------------------------------------------------------------------

// Lazy import so we get the real component (not a module-level mock).
async function renderBanner(workspaceId: string | null) {
  const { LimitBanner } = await import("@/components/billing/limit-banner");
  return render(<LimitBanner workspaceId={workspaceId} />, { wrapper });
}

describe("LimitBanner", () => {
  it("renders nothing when workspaceId is null", async () => {
    // No fetch needed — hook is disabled when workspaceId is null.
    mockFetchEmpty();
    await renderBanner(null);
    expect(screen.queryByTestId("limit-banner")).toBeNull();
  });

  it("renders nothing when the user is unauthenticated (no token)", async () => {
    // useSessionStore accessToken is null by default → hook disabled.
    mockFetchEmpty();
    await renderBanner("ws-001");
    // Query is disabled — banner should not appear.
    expect(screen.queryByTestId("limit-banner")).toBeNull();
  });

  it("renders nothing when all dimensions are ok", async () => {
    useSessionStore.setState({ accessToken: "tok", user: null });
    mockFetchWithLimits(makeLimits("ok", 50));

    const { LimitBanner } = await import("@/components/billing/limit-banner");
    render(<LimitBanner workspaceId="ws-001" />, { wrapper });

    // Wait for potential fetch to resolve; banner should still be absent.
    await waitFor(() => {
      // Either still loading (null) or loaded but all ok (null).
      expect(screen.queryByTestId("limit-banner")).toBeNull();
    });
  });

  it("renders warning banner when worst state is warning", async () => {
    useSessionStore.setState({ accessToken: "tok", user: null });
    mockFetchWithLimits(makeLimits("warning", 85));

    const { LimitBanner } = await import("@/components/billing/limit-banner");
    render(<LimitBanner workspaceId="ws-001" />, { wrapper });

    const banner = await screen.findByTestId("limit-banner");
    expect(banner.getAttribute("data-state")).toBe("warning");
    expect(banner.textContent?.toLowerCase()).toContain("approaching");
    expect(screen.queryByRole("link", { name: /view plans/i })).not.toBeNull();
  });

  it("renders exceeded banner when worst state is exceeded", async () => {
    useSessionStore.setState({ accessToken: "tok", user: null });
    mockFetchWithLimits(makeLimits("exceeded", 100));

    const { LimitBanner } = await import("@/components/billing/limit-banner");
    render(<LimitBanner workspaceId="ws-001" />, { wrapper });

    const banner = await screen.findByTestId("limit-banner");
    expect(banner.getAttribute("data-state")).toBe("exceeded");
    expect(banner.textContent?.toLowerCase()).toMatch(/limit reached|blocked/);
    expect(screen.queryByRole("link", { name: /upgrade/i })).not.toBeNull();
  });
});

// ---------------------------------------------------------------------------
// worstLimitState pure-logic tests
// ---------------------------------------------------------------------------

describe("worstLimitState", () => {
  it("returns ok for null limits", () => {
    expect(worstLimitState(null)).toBe("ok");
  });

  it("returns ok for undefined limits", () => {
    expect(worstLimitState(undefined)).toBe("ok");
  });

  it("returns ok when all dimensions are ok", () => {
    expect(worstLimitState(makeLimits("ok", 50))).toBe("ok");
  });

  it("returns warning when any dimension is warning", () => {
    expect(worstLimitState(makeLimits("warning", 85))).toBe("warning");
  });

  it("returns exceeded when any dimension is exceeded", () => {
    expect(worstLimitState(makeLimits("exceeded", 100))).toBe("exceeded");
  });

  it("exceeded wins over warning", () => {
    const limits: WorkspaceLimits = {
      workspace_id: "ws",
      period: "2026-05-01",
      dimensions: {
        a: { dimension: "a", used: 80, limit: 100, pct: 80, state: "warning" },
        b: { dimension: "b", used: 100, limit: 100, pct: 100, state: "exceeded" },
      },
    };
    expect(worstLimitState(limits)).toBe("exceeded");
  });
});

// ---------------------------------------------------------------------------
// alertingDimensions pure-logic tests
// ---------------------------------------------------------------------------

describe("alertingDimensions", () => {
  it("returns empty array for null limits", () => {
    expect(alertingDimensions(null)).toEqual([]);
  });

  it("returns empty array for undefined limits", () => {
    expect(alertingDimensions(undefined)).toEqual([]);
  });

  it("returns only non-ok dimensions", () => {
    const limits: WorkspaceLimits = {
      workspace_id: "ws",
      period: "2026-05-01",
      dimensions: {
        ok_dim: { dimension: "ok_dim", used: 1, limit: 100, pct: 1, state: "ok" },
        warn_dim: {
          dimension: "warn_dim",
          used: 80,
          limit: 100,
          pct: 80,
          state: "warning",
        },
        exc_dim: {
          dimension: "exc_dim",
          used: 100,
          limit: 100,
          pct: 100,
          state: "exceeded",
        },
      },
    };
    const result = alertingDimensions(limits);
    expect(result).toHaveLength(2);
    const dimNames = result.map((d) => d.dimension);
    expect(dimNames).not.toContain("ok_dim");
    expect(dimNames).toContain("warn_dim");
    expect(dimNames).toContain("exc_dim");
  });

  it("returns empty array when all dimensions are ok", () => {
    const limits: WorkspaceLimits = {
      workspace_id: "ws",
      period: "2026-05-01",
      dimensions: {
        d1: { dimension: "d1", used: 5, limit: 100, pct: 5, state: "ok" },
        d2: { dimension: "d2", used: 0, limit: null, pct: null, state: "ok" },
      },
    };
    expect(alertingDimensions(limits)).toEqual([]);
  });
});
