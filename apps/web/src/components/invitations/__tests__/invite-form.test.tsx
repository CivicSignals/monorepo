// @vitest-environment jsdom
// B6 — InviteForm: render, validation, submit (success + error).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { InviteForm } from "../invite-form";
import { useSessionStore } from "@/store/session";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function makeInvitation(overrides: Record<string, unknown> = {}) {
  return {
    id: "inv-1",
    workspace_id: "ws-1",
    invited_email: "new@example.com",
    role: "member",
    status: "pending",
    invited_by: "user-1",
    accepted_at: null,
    expires_at: new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString(),
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.getState().setSession("tok-admin", {
    id: "user-1",
    email: "admin@example.com",
    name: "Admin",
    email_verified: true,
    created_at: "2026-05-22T00:00:00Z",
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("InviteForm", () => {
  it("renders email and role fields with a submit button", () => {
    render(<InviteForm workspaceId="ws-1" />, { wrapper });
    expect(screen.getByLabelText(/email address/i)).toBeTruthy();
    expect(screen.getByLabelText(/role/i)).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /send invitation/i }),
    ).toBeTruthy();
  });

  it("shows a validation error for an invalid email and does not call the API", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const user = userEvent.setup();
    render(<InviteForm workspaceId="ws-1" />, { wrapper });

    await user.type(screen.getByLabelText(/email address/i), "not-an-email");
    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    expect(await screen.findByText(/valid email/i)).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("submits and shows success message on 201", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(makeInvitation(), 201),
    );
    const user = userEvent.setup();
    render(<InviteForm workspaceId="ws-1" />, { wrapper });

    await user.type(
      screen.getByLabelText(/email address/i),
      "new@example.com",
    );
    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    await waitFor(() => {
      expect(screen.getByText(/invitation sent/i)).toBeTruthy();
    });
  });

  it("shows an error message on 409 conflict", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        {
          type: "https://docs.civicsignals.io/errors/invitation_pending",
          title: "Pending invitation exists",
          status: 409,
          detail: "A pending invitation for this email already exists.",
        },
        409,
      ),
    );
    const user = userEvent.setup();
    render(<InviteForm workspaceId="ws-1" />, { wrapper });

    await user.type(
      screen.getByLabelText(/email address/i),
      "dup@example.com",
    );
    await user.click(screen.getByRole("button", { name: /send invitation/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/pending invitation for this email/i),
      ).toBeTruthy();
    });
  });
});
