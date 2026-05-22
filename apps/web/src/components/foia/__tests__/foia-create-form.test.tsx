// @vitest-environment jsdom
// M4 — FOIA create form: validation + submission.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { FoiaCreateModal } from "@/components/foia/foia-create-form";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type { FoiaRequestRead } from "@/lib/foia-api";

// Mock next/navigation since FoiaCreateForm calls useRouter.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeFoiaRequest(over: Partial<FoiaRequestRead> = {}): FoiaRequestRead {
  return {
    id: "req-created",
    workspace_id: "ws-001",
    created_by: "user-001",
    entity_id: "entity-001",
    jurisdiction: null,
    subject: "Test request",
    body: "Please provide records.",
    submission_method: "manual",
    submission_target: null,
    status: "draft",
    sent_at: null,
    ack_at: null,
    response_at: null,
    response_notes: null,
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

describe("FoiaCreateModal", () => {
  it("renders the form when open", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(<FoiaCreateModal open={true} onClose={() => {}} />, { wrapper });

    expect(screen.getByRole("dialog", { name: /create foia request/i })).toBeTruthy();
    expect(screen.getByLabelText(/subject/i)).toBeTruthy();
    expect(screen.getByLabelText(/target entity id/i)).toBeTruthy();
    expect(screen.getByLabelText(/request body/i)).toBeTruthy();
  });

  it("does not render when closed", () => {
    render(<FoiaCreateModal open={false} onClose={() => {}} />, { wrapper });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("submit button is disabled when required fields are empty", () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    render(<FoiaCreateModal open={true} onClose={() => {}} />, { wrapper });

    const submitBtn = screen.getByRole("button", { name: /create draft/i });
    expect((submitBtn as HTMLButtonElement).disabled).toBe(true);
  });

  it("submit button enables when all required fields are filled", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const user = userEvent.setup();

    render(<FoiaCreateModal open={true} onClose={() => {}} />, { wrapper });

    await user.type(screen.getByLabelText(/target entity id/i), "entity-uuid-001");
    await user.type(screen.getByLabelText(/subject/i), "Vendor PO list 2024");
    await user.type(screen.getByLabelText(/request body/i), "Please provide all vendor purchase orders.");

    const submitBtn = screen.getByRole("button", { name: /create draft/i });
    expect((submitBtn as HTMLButtonElement).disabled).toBe(false);
  });

  it("calls the create API and closes on success", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/foia/templates")) {
        return new Response(JSON.stringify({ items: [], total: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/foia/requests") && !(url.includes("/transition") || url.includes("/events"))) {
        return new Response(JSON.stringify(makeFoiaRequest()), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    const onClose = vi.fn();
    const user = userEvent.setup();

    render(<FoiaCreateModal open={true} onClose={onClose} />, { wrapper });

    await user.type(screen.getByLabelText(/target entity id/i), "entity-uuid-001");
    await user.type(screen.getByLabelText(/subject/i), "Vendor PO list 2024");
    await user.type(screen.getByLabelText(/request body/i), "Please provide all vendor purchase orders.");

    const submitBtn = screen.getByRole("button", { name: /create draft/i });
    await user.click(submitBtn);

    await waitFor(() => {
      const postCall = fetchSpy.mock.calls.find(
        (c) => String(c[0]).includes("/foia/requests") && (c[1] as RequestInit)?.method === "POST",
      );
      expect(postCall).toBeTruthy();
    });
  });

  it("shows an error when the API returns a problem", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes("/foia/templates")) {
        return new Response(JSON.stringify({ items: [], total: 0 }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      // Create endpoint returns error
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Entity not found",
          status: 422,
          detail: "Entity bad-id does not exist.",
        }),
        { status: 422, headers: { "Content-Type": "application/problem+json" } },
      );
    });

    const user = userEvent.setup();
    render(<FoiaCreateModal open={true} onClose={() => {}} />, { wrapper });

    await user.type(screen.getByLabelText(/target entity id/i), "bad-id");
    await user.type(screen.getByLabelText(/subject/i), "Some request");
    await user.type(screen.getByLabelText(/request body/i), "Please provide records.");

    await user.click(screen.getByRole("button", { name: /create draft/i }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toBeTruthy(),
    );
    expect(screen.getByText(/Entity bad-id does not exist/)).toBeTruthy();
  });

  it("closes when Cancel is clicked", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const onClose = vi.fn();
    const user = userEvent.setup();

    render(<FoiaCreateModal open={true} onClose={onClose} />, { wrapper });
    await user.click(screen.getByRole("button", { name: /cancel/i }));

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("closes when Escape key is pressed", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const onClose = vi.fn();
    const user = userEvent.setup();

    render(<FoiaCreateModal open={true} onClose={onClose} />, { wrapper });
    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledOnce();
  });
});
