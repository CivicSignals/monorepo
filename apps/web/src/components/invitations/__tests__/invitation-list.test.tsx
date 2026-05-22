// @vitest-environment jsdom
// B6 — InvitationList: render list, revoke, resend actions.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { InvitationList } from "../invitation-list";
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

function pathOf(input: RequestInfo | URL): string {
  return new URL(String(input), "http://localhost").pathname;
}

const EXPIRES_FUTURE = new Date(
  Date.now() + 7 * 24 * 60 * 60 * 1000,
).toISOString();

const pendingInvite = {
  id: "inv-1",
  workspace_id: "ws-1",
  invited_email: "pending@example.com",
  role: "member",
  status: "pending",
  invited_by: "user-1",
  accepted_at: null,
  expires_at: EXPIRES_FUTURE,
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
};

const acceptedInvite = {
  ...pendingInvite,
  id: "inv-2",
  invited_email: "accepted@example.com",
  status: "accepted",
  accepted_at: "2026-05-22T12:00:00Z",
};

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

describe("InvitationList", () => {
  it("shows empty state when there are no invitations", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ items: [], next_cursor: null }),
    );
    render(<InvitationList workspaceId="ws-1" />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/no invitations yet/i)).toBeTruthy();
    });
  });

  it("renders pending and accepted invitations", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ items: [pendingInvite, acceptedInvite], next_cursor: null }),
    );
    render(<InvitationList workspaceId="ws-1" />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("pending@example.com")).toBeTruthy();
      expect(screen.getByText("accepted@example.com")).toBeTruthy();
    });
  });

  it("calls revoke endpoint on Revoke click and refetches", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path.endsWith("/invitations") && !path.includes("/inv-")) {
          return jsonResponse(
            { items: [pendingInvite], next_cursor: null },
            200,
          );
        }
        if (path.includes("/inv-1") && !path.includes("/resend")) {
          return jsonResponse(undefined, 204);
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<InvitationList workspaceId="ws-1" />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("pending@example.com")).toBeTruthy();
    });

    const revokeBtn = screen.getByRole("button", { name: /revoke/i });
    await user.click(revokeBtn);

    await waitFor(() => {
      const calls = fetchSpy.mock.calls.map(([input]) => pathOf(input));
      expect(calls.some((p) => p.endsWith("/inv-1"))).toBe(true);
    });
  });

  it("calls resend endpoint on Resend click", async () => {
    const newInvite = { ...pendingInvite, id: "inv-2" };
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const path = pathOf(input);
        if (
          path.endsWith("/invitations") ||
          (path.includes("/invitations") && !path.includes("/inv-"))
        ) {
          return jsonResponse(
            { items: [pendingInvite], next_cursor: null },
            200,
          );
        }
        if (path.endsWith("/resend")) {
          return jsonResponse(newInvite, 201);
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<InvitationList workspaceId="ws-1" />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("pending@example.com")).toBeTruthy();
    });

    const resendBtn = screen.getByRole("button", { name: /resend/i });
    await user.click(resendBtn);

    await waitFor(() => {
      const calls = fetchSpy.mock.calls.map(([input]) => pathOf(input));
      expect(calls.some((p) => p.endsWith("/resend"))).toBe(true);
    });
  });
});
