// @vitest-environment jsdom
// M4 — FOIA detail page: renders fields, timeline, and transition buttons.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { FoiaDetail } from "@/components/foia/foia-detail";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type { FoiaRequestRead, FoiaRequestEventRead } from "@/lib/foia-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const BASE_REQUEST: FoiaRequestRead = {
  id: "req-001",
  workspace_id: "ws-001",
  created_by: "user-001",
  entity_id: "entity-001",
  jurisdiction: "TX-PIA",
  subject: "Vendor contracts FY24 > $10k",
  body: "Pursuant to the Texas Public Information Act, please provide all vendor contracts.",
  submission_method: "email",
  submission_target: "records@plano.gov",
  status: "draft",
  sent_at: null,
  ack_at: null,
  response_at: null,
  response_notes: null,
  created_at: "2026-05-01T10:00:00Z",
  updated_at: "2026-05-01T10:00:00Z",
};

const SENT_REQUEST: FoiaRequestRead = {
  ...BASE_REQUEST,
  status: "sent",
  sent_at: "2026-05-10T14:00:00Z",
};

const RESPONDED_REQUEST: FoiaRequestRead = {
  ...BASE_REQUEST,
  status: "response",
  sent_at: "2026-05-10T14:00:00Z",
  ack_at: "2026-05-15T09:00:00Z",
  response_at: "2026-05-22T11:00:00Z",
  response_notes: "Partial response received.",
};

const EVENTS: FoiaRequestEventRead[] = [
  {
    id: "evt-1",
    request_id: "req-001",
    actor_id: "user-001",
    from_status: "draft",
    to_status: "sent",
    occurred_at: "2026-05-10T14:00:00Z",
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pathOf(input: RequestInfo | URL): string {
  return new URL(String(input), "http://localhost").pathname;
}

function setupFetch(req: FoiaRequestRead, events: FoiaRequestEventRead[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = pathOf(input);
    if (path === `/api/v1/foia/requests/${req.id}`) return jsonResponse(req);
    if (path === `/api/v1/foia/requests/${req.id}/events`) return jsonResponse(events);
    // Transition endpoint
    if (path === `/api/v1/foia/requests/${req.id}/transition`) {
      const body = await (input as Request).json?.().catch(() => ({})) ?? {};
      return jsonResponse({ ...req, status: (body as { status: string }).status });
    }
    // Fallback: list endpoint etc.
    return jsonResponse({ items: [], next_cursor: null });
  });
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ accessToken: "test-jwt", user: null });
  useUiStore.setState({ activeWorkspaceId: "ws-001" });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSessionStore.setState({ accessToken: null, user: null });
  useUiStore.setState({ activeWorkspaceId: null });
});

describe("FoiaDetail", () => {
  it("renders the request subject as a heading", async () => {
    setupFetch(BASE_REQUEST);
    render(<FoiaDetail id="req-001" />, { wrapper });
    expect(await screen.findByRole("heading", { name: /Vendor contracts FY24/i })).toBeTruthy();
  });

  it("renders the request body", async () => {
    setupFetch(BASE_REQUEST);
    render(<FoiaDetail id="req-001" />, { wrapper });
    await screen.findByRole("heading", { name: /Vendor contracts FY24/i });
    expect(screen.getByText(/Pursuant to the Texas Public Information Act/)).toBeTruthy();
  });

  it("renders the jurisdiction", async () => {
    setupFetch(BASE_REQUEST);
    render(<FoiaDetail id="req-001" />, { wrapper });
    await screen.findByRole("heading", { name: /Vendor contracts FY24/i });
    expect(screen.getAllByText("TX-PIA").length).toBeGreaterThan(0);
  });

  it("renders the status badge", async () => {
    setupFetch(BASE_REQUEST);
    render(<FoiaDetail id="req-001" />, { wrapper });
    await screen.findByRole("heading", { name: /Vendor contracts FY24/i });
    expect(screen.getAllByText("Draft").length).toBeGreaterThan(0);
  });

  it("renders the timeline with transition events", async () => {
    setupFetch(SENT_REQUEST, EVENTS);
    render(<FoiaDetail id="req-001" />, { wrapper });

    await screen.findByRole("heading", { name: /Vendor contracts FY24/i });

    // Timeline section heading
    expect(screen.getByRole("region", { name: /status timeline/i })).toBeTruthy();
    // Event transition text
    await waitFor(() =>
      expect(screen.getByText(/Awaiting response/)).toBeTruthy(),
    );
  });

  it("shows transition button for draft → sent", async () => {
    setupFetch(BASE_REQUEST);
    render(<FoiaDetail id="req-001" />, { wrapper });

    await screen.findByRole("heading", { name: /Vendor contracts FY24/i });
    const btn = await screen.findByRole("button", { name: /transition to sent/i });
    expect(btn).toBeTruthy();
  });

  it("shows transition button for sent → ack", async () => {
    setupFetch(SENT_REQUEST, EVENTS);
    render(<FoiaDetail id="req-001" />, { wrapper });

    await screen.findByRole("heading", { name: /Vendor contracts FY24/i });
    const btn = await screen.findByRole("button", { name: /transition to ack/i });
    expect(btn).toBeTruthy();
  });

  it("shows no transition buttons for terminal status (response)", async () => {
    setupFetch(RESPONDED_REQUEST);
    render(<FoiaDetail id="req-001" />, { wrapper });

    await screen.findByRole("heading", { name: /Vendor contracts FY24/i });
    await waitFor(() =>
      expect(screen.getByText(/final status/i)).toBeTruthy(),
    );
    // No transition buttons should be present
    expect(screen.queryByRole("button", { name: /transition to/i })).toBeNull();
  });

  it("shows response notes when present", async () => {
    setupFetch(RESPONDED_REQUEST);
    render(<FoiaDetail id="req-001" />, { wrapper });

    expect(await screen.findByText("Partial response received.")).toBeTruthy();
  });

  it("shows not-found message for unknown id", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "FOIA request not found",
          status: 404,
        }),
        { status: 404, headers: { "Content-Type": "application/problem+json" } },
      ),
    );

    render(<FoiaDetail id="nonexistent-id" />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText(/FOIA request not found/)).toBeTruthy(),
    );
  });

  it("sends X-Workspace-Id header on detail requests", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(BASE_REQUEST),
    );

    render(<FoiaDetail id="req-001" />, { wrapper });

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    const detailCall = fetchSpy.mock.calls.find((c) =>
      String(c[0]).includes("/foia/requests/req-001"),
    );
    expect(detailCall).toBeTruthy();
    if (detailCall) {
      const headers = new Headers((detailCall[1] as RequestInit).headers);
      expect(headers.get("X-Workspace-Id")).toBe("ws-001");
    }
  });
});
