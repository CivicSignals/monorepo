// @vitest-environment jsdom
// M3 — FoiaAttachmentUpload: file picker + upload mutation tests.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { FoiaAttachmentUpload } from "@/components/foia/foia-attachment-upload";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type { FoiaAttachmentRead } from "@/lib/foia-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const FAKE_ATT: FoiaAttachmentRead = {
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

function setupFetch(response: FoiaAttachmentRead, status = 201): void {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(response), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
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

describe("FoiaAttachmentUpload", () => {
  it("renders the file input and disabled upload button", () => {
    render(<FoiaAttachmentUpload requestId="req-001" />, { wrapper });
    expect(screen.getByLabelText(/choose response file/i)).toBeTruthy();
    const btn = screen.getByRole("button", { name: /upload response/i });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("enables the upload button once a file is selected", () => {
    render(<FoiaAttachmentUpload requestId="req-001" />, { wrapper });

    const input = screen.getByLabelText(/choose response file/i) as HTMLInputElement;
    const file = new File(["pdf content"], "response.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    const btn = screen.getByRole("button", { name: /upload response/i });
    expect((btn as HTMLButtonElement).disabled).toBe(false);
  });

  it("shows success message after upload", async () => {
    setupFetch(FAKE_ATT);
    render(<FoiaAttachmentUpload requestId="req-001" />, { wrapper });

    const input = screen.getByLabelText(/choose response file/i) as HTMLInputElement;
    const file = new File(["pdf"], "response.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    const btn = screen.getByRole("button", { name: /upload response/i });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(screen.getByRole("status")).toBeTruthy(),
    );
    expect(screen.getByRole("status").textContent).toContain("extraction queued");
  });

  it("shows an error message when upload fails", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ type: "about:blank", title: "Server Error", status: 500 }),
        { status: 500, headers: { "Content-Type": "application/problem+json" } },
      ),
    );

    render(<FoiaAttachmentUpload requestId="req-001" />, { wrapper });

    const input = screen.getByLabelText(/choose response file/i) as HTMLInputElement;
    const file = new File(["pdf"], "error.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /upload response/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );
  });

  it("sends X-Workspace-Id and Authorization headers", async () => {
    setupFetch(FAKE_ATT);
    render(<FoiaAttachmentUpload requestId="req-001" />, { wrapper });

    const input = screen.getByLabelText(/choose response file/i) as HTMLInputElement;
    const file = new File(["pdf"], "response.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: /upload response/i }));

    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalled());

    const [url, init] = vi.mocked(fetch).mock.calls[0] ?? [];
    expect(String(url)).toContain("/foia/requests/req-001/attachments");
    const headers = new Headers((init as RequestInit).headers);
    expect(headers.get("X-Workspace-Id")).toBe("ws-001");
    expect(headers.get("Authorization")).toBe("Bearer test-jwt");
  });
});
