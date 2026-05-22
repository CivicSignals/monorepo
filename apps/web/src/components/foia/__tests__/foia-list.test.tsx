// @vitest-environment jsdom
// M4 — FOIA list page: renders requests, status filter, and load-more.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { FoiaList } from "@/components/foia/foia-list";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type { FoiaRequestRead, FoiaRequestPage } from "@/lib/foia-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeFoiaRequest(over: Partial<FoiaRequestRead>): FoiaRequestRead {
  return {
    id: "00000000-0000-7000-8000-000000000001",
    workspace_id: "ws-001",
    created_by: "user-001",
    entity_id: "entity-001",
    jurisdiction: "TX-PIA",
    subject: "Vendor contracts FY24",
    body: "Please provide all contracts exceeding $10,000 for fiscal year 2024.",
    submission_method: "email",
    submission_target: "records@plano.gov",
    status: "draft",
    sent_at: null,
    ack_at: null,
    response_at: null,
    response_notes: null,
    created_at: "2026-05-01T10:00:00Z",
    updated_at: "2026-05-01T10:00:00Z",
    ...over,
  };
}

function jsonPage(items: FoiaRequestRead[], nextCursor: string | null = null): Response {
  const body: FoiaRequestPage = { items, next_cursor: nextCursor };
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
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

describe("FoiaList", () => {
  it("renders a list of FOIA requests from the API", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonPage([
        makeFoiaRequest({ id: "req-1", subject: "Vendor contracts FY24", status: "draft" }),
        makeFoiaRequest({ id: "req-2", subject: "Bid evaluation scores", status: "sent" }),
      ]),
    );

    render(<FoiaList />, { wrapper });

    expect(await screen.findByText("Vendor contracts FY24")).toBeTruthy();
    expect(screen.getByText("Bid evaluation scores")).toBeTruthy();
  });

  it("renders status badges for each request", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonPage([
        makeFoiaRequest({ id: "req-1", subject: "Draft request", status: "draft" }),
        makeFoiaRequest({ id: "req-2", subject: "Sent request", status: "sent" }),
        makeFoiaRequest({ id: "req-3", subject: "Responded request", status: "response" }),
      ]),
    );

    render(<FoiaList />, { wrapper });

    await screen.findByText("Draft request");
    // Status labels appear both in the filter dropdown and status badges — use getAllByText.
    expect(screen.getAllByText("Draft").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Awaiting").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Responded").length).toBeGreaterThanOrEqual(1);
  });

  it("each request subject links to the detail page", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonPage([makeFoiaRequest({ id: "req-abc", subject: "My FOIA request" })]),
    );

    render(<FoiaList />, { wrapper });

    const link = await screen.findByRole("link", { name: "My FOIA request" });
    expect(link.getAttribute("href")).toBe("/foia/req-abc");
  });

  it("shows a count line after loading", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonPage([
        makeFoiaRequest({ id: "req-1", subject: "Request A" }),
        makeFoiaRequest({ id: "req-2", subject: "Request B" }),
      ]),
    );

    render(<FoiaList />, { wrapper });

    await waitFor(() => expect(screen.getByText(/Showing 2 requests/)).toBeTruthy());
  });

  it("filters requests by status when status filter changes", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonPage([]));
    const user = userEvent.setup();

    render(<FoiaList />, { wrapper });

    const select = await screen.findByRole("combobox", { name: /filter by status/i });
    await user.selectOptions(select, "sent");

    await waitFor(() => {
      const calls = fetchSpy.mock.calls;
      const hasSentParam = calls.some((c) => String(c[0]).includes("status=sent"));
      expect(hasSentParam).toBe(true);
    });
  });

  it("shows an error alert when the API returns an error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Internal Server Error",
          status: 500,
          detail: "Something went wrong",
        }),
        { status: 500, headers: { "Content-Type": "application/problem+json" } },
      ),
    );

    render(<FoiaList />, { wrapper });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Something went wrong");
  });

  it("shows a Load more button when next_cursor is set", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonPage([makeFoiaRequest({ id: "req-1", subject: "Request A" })], "cursor-xyz"),
    );

    render(<FoiaList />, { wrapper });

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /load more/i })).toBeTruthy(),
    );
  });

  it("shows empty state when there are no requests", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonPage([]));

    render(<FoiaList />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText(/No FOIA requests yet/)).toBeTruthy(),
    );
  });

  it("sends X-Workspace-Id header on list requests", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonPage([]));

    render(<FoiaList />, { wrapper });

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

    const [, init] = fetchSpy.mock.calls[0];
    const headers = new Headers((init as RequestInit).headers);
    expect(headers.get("X-Workspace-Id")).toBe("ws-001");
  });
});
