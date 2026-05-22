// @vitest-environment jsdom
// J4 — NewItemModal: form renders, mutation calls the right endpoint.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NewItemModal } from "@/components/pipeline/new-item-modal";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type { PipelineItem } from "@/lib/pipeline-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makePipelineItem(over: Partial<PipelineItem> = {}): PipelineItem {
  return {
    id: "item-001",
    workspace_id: "ws-001",
    stage_id: "stage-001",
    signal_id: null,
    owner_id: null,
    title: "Manual opportunity",
    notes: null,
    value_estimate: null,
    status: "active",
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...over,
  };
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

// ---- Render / open/close ---------------------------------------------------

describe("NewItemModal — open/close behaviour", () => {
  it("renders the dialog when open=true", () => {
    render(<NewItemModal open={true} onClose={() => {}} />, { wrapper });
    expect(screen.getByRole("dialog", { name: /add pipeline item/i })).toBeTruthy();
  });

  it("does not render when open=false", () => {
    render(<NewItemModal open={false} onClose={() => {}} />, { wrapper });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("renders the title input and submit button when open", () => {
    render(<NewItemModal open={true} onClose={() => {}} />, { wrapper });
    expect(screen.getByLabelText(/title/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /add item/i })).toBeTruthy();
  });

  it("renders optional value and notes fields", () => {
    render(<NewItemModal open={true} onClose={() => {}} />, { wrapper });
    expect(screen.getByLabelText(/estimated value/i)).toBeTruthy();
    expect(screen.getByLabelText(/notes/i)).toBeTruthy();
  });

  it("calls onClose when Cancel is clicked", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<NewItemModal open={true} onClose={onClose} />, { wrapper });
    await user.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("calls onClose when Escape key is pressed", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<NewItemModal open={true} onClose={onClose} />, { wrapper });
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });
});

// ---- Form validation -------------------------------------------------------

describe("NewItemModal — form validation", () => {
  it("submit button is disabled when title is empty", () => {
    render(<NewItemModal open={true} onClose={() => {}} />, { wrapper });
    const btn = screen.getByRole("button", { name: /add item/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("submit button enables when title is filled", async () => {
    const user = userEvent.setup();
    render(<NewItemModal open={true} onClose={() => {}} />, { wrapper });
    await user.type(screen.getByLabelText(/title/i), "City Hall contract");
    const btn = screen.getByRole("button", { name: /add item/i }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });
});

// ---- Mutation wiring -------------------------------------------------------

describe("NewItemModal — mutation", () => {
  it("POSTs to /pipeline/items/manual with title on submit", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(makePipelineItem()), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const user = userEvent.setup();
    render(<NewItemModal open={true} onClose={() => {}} />, { wrapper });

    await user.type(screen.getByLabelText(/title/i), "City Hall contract");
    await user.click(screen.getByRole("button", { name: /add item/i }));

    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(
        (c) =>
          String(c[0]).includes("/pipeline/items/manual") &&
          (c[1] as RequestInit)?.method === "POST",
      );
      expect(call).toBeTruthy();
    });
  });

  it("includes value_estimate in the request body when provided", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(makePipelineItem({ value_estimate: "50000.00" })), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const user = userEvent.setup();
    render(<NewItemModal open={true} onClose={() => {}} />, { wrapper });

    await user.type(screen.getByLabelText(/title/i), "Valued deal");
    await user.type(screen.getByLabelText(/estimated value/i), "50000");
    await user.click(screen.getByRole("button", { name: /add item/i }));

    await waitFor(() => {
      const call = fetchSpy.mock.calls.find(
        (c) =>
          String(c[0]).includes("/pipeline/items/manual") &&
          (c[1] as RequestInit)?.method === "POST",
      );
      expect(call).toBeTruthy();
      if (call) {
        const body = JSON.parse((call[1] as RequestInit)?.body as string) as {
          value_estimate?: string;
        };
        expect(body.value_estimate).toBe("50000");
      }
    });
  });

  it("closes the dialog on successful submission", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(makePipelineItem()), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<NewItemModal open={true} onClose={onClose} />, { wrapper });

    await user.type(screen.getByLabelText(/title/i), "Closing deal");
    await user.click(screen.getByRole("button", { name: /add item/i }));

    await waitFor(() => expect(onClose).toHaveBeenCalledOnce());
  });

  it("shows an error alert when the API returns a problem", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Stage not found",
          status: 404,
          detail: "No such stage in this workspace.",
        }),
        { status: 404, headers: { "Content-Type": "application/problem+json" } },
      ),
    );

    const user = userEvent.setup();
    render(<NewItemModal open={true} onClose={() => {}} />, { wrapper });

    await user.type(screen.getByLabelText(/title/i), "Bad stage deal");
    await user.click(screen.getByRole("button", { name: /add item/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/No such stage in this workspace/i)).toBeTruthy();
  });
});
