// @vitest-environment jsdom
// M3 — FoiaAttachmentList: renders attachments + extraction status.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { FoiaAttachmentList } from "@/components/foia/foia-attachment-list";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type { FoiaAttachmentPage, FoiaAttachmentRead } from "@/lib/foia-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const PENDING_ATT: FoiaAttachmentRead = {
  id: "att-001",
  foia_request_id: "req-001",
  raw_document_id: "raw-001",
  extraction_job_id: "job-001",
  filename: "response.pdf",
  content_type: "application/pdf",
  uploaded_by: "user-001",
  uploaded_at: "2026-05-22T12:00:00Z",
  extraction_status: "pending",
};

const DONE_ATT: FoiaAttachmentRead = {
  ...PENDING_ATT,
  id: "att-002",
  filename: "complete.pdf",
  extraction_status: "done",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function setupFetch(page: FoiaAttachmentPage, signals: unknown[] = []) {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path.includes("/signals")) return jsonResponse(signals);
    if (path.includes("/attachments")) return jsonResponse(page);
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

describe("FoiaAttachmentList", () => {
  it("shows empty state when no attachments", async () => {
    setupFetch({ items: [], next_cursor: null });
    render(<FoiaAttachmentList requestId="req-001" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText(/no response documents/i)).toBeTruthy(),
    );
  });

  it("renders attachment filename", async () => {
    setupFetch({ items: [PENDING_ATT], next_cursor: null });
    render(<FoiaAttachmentList requestId="req-001" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("response.pdf")).toBeTruthy(),
    );
  });

  it("shows Queued status badge for pending attachment", async () => {
    setupFetch({ items: [PENDING_ATT], next_cursor: null });
    render(<FoiaAttachmentList requestId="req-001" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("Queued")).toBeTruthy(),
    );
  });

  it("shows Done status badge for completed attachment", async () => {
    setupFetch({ items: [DONE_ATT], next_cursor: null });
    render(<FoiaAttachmentList requestId="req-001" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("Done")).toBeTruthy(),
    );
  });

  it("shows 'Show linked signals' toggle for done attachments", async () => {
    setupFetch({ items: [DONE_ATT], next_cursor: null });
    render(<FoiaAttachmentList requestId="req-001" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText(/show linked signals/i)).toBeTruthy(),
    );
  });

  it("renders multiple attachments", async () => {
    setupFetch({ items: [PENDING_ATT, DONE_ATT], next_cursor: null });
    render(<FoiaAttachmentList requestId="req-001" />, { wrapper });
    await waitFor(() =>
      expect(screen.getByText("response.pdf")).toBeTruthy(),
    );
    await waitFor(() =>
      expect(screen.getByText("complete.pdf")).toBeTruthy(),
    );
  });

  it("sends X-Workspace-Id on attachment requests", async () => {
    setupFetch({ items: [], next_cursor: null });
    render(<FoiaAttachmentList requestId="req-001" />, { wrapper });
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());

    const call = vi.mocked(fetch).mock.calls.find((c) =>
      String(c[0]).includes("/attachments"),
    );
    expect(call).toBeTruthy();
    if (call) {
      const headers = new Headers((call[1] as RequestInit).headers);
      expect(headers.get("X-Workspace-Id")).toBe("ws-001");
    }
  });
});
