// @vitest-environment jsdom
// N5 — BillingSettings: current plan display, usage meters, change-plan buttons,
// portal button. Tests the component with fetch mocks.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
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

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const WORKSPACE_ID = "ws-n5-test";

// Mock workspace hooks so the component renders without a real API.
vi.mock("@/hooks/use-workspaces", () => ({
  useWorkspaces: () => ({
    data: [{ id: WORKSPACE_ID, name: "Test Workspace" }],
    isLoading: false,
  }),
  useActiveWorkspace: () => ({ id: WORKSPACE_ID, name: "Test Workspace" }),
}));

function makePlanInfo(plan = "starter") {
  return {
    workspace_id: WORKSPACE_ID,
    effective_plan: {
      plan,
      display_name: plan.charAt(0).toUpperCase() + plan.slice(1),
      features: ["managed_scrapers", "smart_search"],
      limits: {
        seats: 10,
        tracked_entities: 5000,
        smart_searches_per_month: 100,
        contact_exports_per_month: 500,
        saved_searches: 50,
        api_requests_per_month: 50000,
        ai_runs_per_month: 5000,
      },
    },
    all_features: ["managed_scrapers", "smart_search"],
  };
}

function makeUsage() {
  return {
    workspace_id: WORKSPACE_ID,
    period: "2026-05-01",
    dimensions: {
      ai_runs_per_month: {
        dimension: "ai_runs_per_month",
        used: 250,
        limit: 5000,
        pct_used: 5.0,
      },
    },
  };
}

function makeLimits() {
  return {
    workspace_id: WORKSPACE_ID,
    period: "2026-05-01",
    dimensions: {
      ai_runs_per_month: {
        dimension: "ai_runs_per_month",
        used: 250,
        limit: 5000,
        pct: 5.0,
        state: "ok",
      },
    },
  };
}

// Mock fetch dispatcher: route by URL path.
function setupFetchMocks(
  opts: {
    plan?: object;
    usage?: object;
    limits?: object;
    changePlanResponse?: object;
    changePlanStatus?: number;
    portalUrl?: string;
    portalStatus?: number;
  } = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname;

    if (path.endsWith("/billing/plan")) {
      return jsonResponse(opts.plan ?? makePlanInfo());
    }
    if (path.endsWith("/billing/usage")) {
      return jsonResponse(opts.usage ?? makeUsage());
    }
    if (path.endsWith("/billing/limits")) {
      return jsonResponse(opts.limits ?? makeLimits());
    }
    if (path.endsWith("/billing/change-plan")) {
      const status = opts.changePlanStatus ?? 200;
      return jsonResponse(opts.changePlanResponse ?? makePlanInfo("pro"), status);
    }
    if (path.endsWith("/billing/portal-session")) {
      const status = opts.portalStatus ?? 200;
      return jsonResponse(
        { url: opts.portalUrl ?? "https://billing.stripe.com/p/test" },
        status,
      );
    }
    // Workspaces call.
    if (path.endsWith("/workspaces")) {
      return jsonResponse({ items: [], cursor: null });
    }
    // Default: not found.
    return new Response(
      JSON.stringify({ status: 404, title: "Not found", type: "about:blank" }),
      {
        status: 404,
        headers: { "Content-Type": "application/json" },
      },
    );
  });
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ accessToken: "jwt-test", user: null });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSessionStore.setState({ accessToken: null, user: null });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

async function renderBillingSettings() {
  const { BillingSettings } = await import(
    "@/app/settings/billing/billing-settings"
  );
  return render(<BillingSettings />, { wrapper });
}

describe("BillingSettings", () => {
  it("renders the billing settings container", async () => {
    setupFetchMocks({ plan: makePlanInfo("starter") });
    await renderBillingSettings();

    await waitFor(() => {
      expect(screen.queryByTestId("billing-settings")).not.toBeNull();
    });
  });

  it("shows the current plan name", async () => {
    setupFetchMocks({ plan: makePlanInfo("starter") });
    await renderBillingSettings();

    await waitFor(() => {
      expect(screen.queryByTestId("billing-settings")).not.toBeNull();
    });

    // Current plan section should show "Starter" somewhere
    const container = screen.queryByTestId("billing-settings");
    expect(container?.textContent?.toLowerCase()).toContain("starter");
  });

  it("shows change-plan buttons for self-serve plans", async () => {
    setupFetchMocks({ plan: makePlanInfo("solo") });
    await renderBillingSettings();

    await waitFor(() => {
      expect(screen.queryByTestId("billing-settings")).not.toBeNull();
    });

    // Should have buttons for solo, starter, pro
    expect(screen.queryByTestId("change-plan-solo")).not.toBeNull();
    expect(screen.queryByTestId("change-plan-starter")).not.toBeNull();
    expect(screen.queryByTestId("change-plan-pro")).not.toBeNull();
  });

  it("marks the current plan button as Current and disabled", async () => {
    setupFetchMocks({ plan: makePlanInfo("starter") });
    await renderBillingSettings();

    await waitFor(() => {
      expect(screen.queryByTestId("billing-settings")).not.toBeNull();
    });

    const starterBtn = screen.queryByTestId(
      "change-plan-starter",
    ) as HTMLButtonElement | null;
    expect(starterBtn).not.toBeNull();
    expect(starterBtn?.disabled).toBe(true);
    expect(starterBtn?.textContent?.toLowerCase()).toMatch(/current/);
  });

  it("renders the portal open button", async () => {
    setupFetchMocks();
    await renderBillingSettings();

    await waitFor(() => {
      expect(screen.queryByTestId("billing-settings")).not.toBeNull();
    });

    expect(screen.queryByTestId("open-portal-btn")).not.toBeNull();
  });

  it("calls change-plan API when upgrade button is clicked", async () => {
    const fetchSpy = setupFetchMocks({ plan: makePlanInfo("solo") });
    const user = userEvent.setup();

    await renderBillingSettings();

    await waitFor(() => {
      expect(screen.queryByTestId("change-plan-pro")).not.toBeNull();
    });

    const proBtn = screen.queryByTestId("change-plan-pro") as HTMLButtonElement;
    await user.click(proBtn);

    await waitFor(() => {
      const changePlanCall = fetchSpy.mock.calls.find(([input]) =>
        String(input).includes("/billing/change-plan"),
      );
      expect(changePlanCall).toBeTruthy();
    });
  });

  it("shows success message after plan change", async () => {
    setupFetchMocks({ plan: makePlanInfo("solo") });
    const user = userEvent.setup();

    await renderBillingSettings();

    await waitFor(() => {
      expect(screen.queryByTestId("change-plan-pro")).not.toBeNull();
    });

    const proBtn = screen.queryByTestId("change-plan-pro") as HTMLButtonElement;
    await user.click(proBtn);

    await waitFor(() => {
      const successMsg = screen.queryByRole("status");
      expect(successMsg?.textContent?.toLowerCase()).toMatch(
        /plan changed successfully/,
      );
    });
  });

  it("calls portal-session API when portal button is clicked", async () => {
    const fetchSpy = setupFetchMocks();
    const user = userEvent.setup();

    // Mock window.location.href assignment (jsdom doesn't support navigation).
    const locationMock = { href: "http://localhost/settings/billing" };
    Object.defineProperty(window, "location", {
      value: locationMock,
      writable: true,
    });

    await renderBillingSettings();

    await waitFor(() => {
      expect(screen.queryByTestId("open-portal-btn")).not.toBeNull();
    });

    const portalBtn = screen.queryByTestId(
      "open-portal-btn",
    ) as HTMLButtonElement;
    await user.click(portalBtn);

    await waitFor(() => {
      const portalCall = fetchSpy.mock.calls.find(([input]) =>
        String(input).includes("/billing/portal-session"),
      );
      expect(portalCall).toBeTruthy();
    });
  });
});
