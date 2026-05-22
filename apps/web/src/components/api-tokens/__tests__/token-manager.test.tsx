// @vitest-environment jsdom
// B8 — TokenManager: create (revealed once), list (no secret), revoke.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { TokenManager } from "../token-manager";
import { useSessionStore } from "@/store/session";
import type { ApiToken } from "@/lib/api-tokens-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
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

function makeToken(over: Partial<ApiToken>): ApiToken {
  return {
    id: "tok-1",
    token_type: "personal",
    name: "CLI",
    token_prefix: "cs_pat_ab12",
    scopes: ["signals:read"],
    workspace_id: null,
    user_id: "user-1",
    created_by_user_id: "user-1",
    last_used_at: null,
    expires_at: null,
    revoked_at: null,
    created_at: "2026-05-22T00:00:00Z",
    ...over,
  };
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({
    accessToken: "jwt-access",
    user: {
      id: "user-1",
      email: "a@b.co",
      name: null,
      email_verified: true,
      created_at: "2026-05-22T00:00:00Z",
    },
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSessionStore.setState({ accessToken: null, user: null });
});

describe("TokenManager (personal)", () => {
  it("lists tokens without ever showing a secret", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = pathOf(input);
      if (path === "/api/v1/auth/tokens/scopes") {
        return jsonResponse({ scopes: ["signals:read", "signals:write"] });
      }
      if (path === "/api/v1/auth/tokens") {
        return jsonResponse({ items: [makeToken({ name: "CLI token" })] });
      }
      return jsonResponse({ items: [] });
    });

    render(<TokenManager kind="personal" />, { wrapper });

    const list = await screen.findByTestId("token-list");
    expect(within(list).queryByText("CLI token")).not.toBeNull();
    // No reveal dialog rendered, so no secret is ever shown on a list view.
    expect(screen.queryByTestId("token-reveal")).toBeNull();
  });

  it("creates a token and reveals the secret exactly once", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = pathOf(input);
        if (path === "/api/v1/auth/tokens/scopes") {
          return jsonResponse({ scopes: ["signals:read", "signals:write"] });
        }
        if (path === "/api/v1/auth/tokens" && init?.method === "POST") {
          return jsonResponse(
            {
              ...makeToken({ id: "tok-new", name: "New" }),
              token: "cs_pat_SECRET",
            },
            201,
          );
        }
        if (path === "/api/v1/auth/tokens") {
          return jsonResponse({ items: [] });
        }
        return jsonResponse({ items: [] });
      });

    const user = userEvent.setup();
    render(<TokenManager kind="personal" />, { wrapper });

    await screen.findByText(/signals:read/);
    await user.type(screen.getByLabelText(/token name/i), "New");
    await user.click(screen.getByRole("button", { name: /create token/i }));

    // The secret appears once, in the reveal dialog.
    const reveal = await screen.findByTestId("token-reveal");
    expect(within(reveal).getByTestId("token-secret").textContent).toBe(
      "cs_pat_SECRET",
    );

    const createCall = fetchSpy.mock.calls.find(
      (c) => pathOf(c[0]) === "/api/v1/auth/tokens" && c[1]?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(String(createCall?.[1]?.body)).toContain("New");

    // Dismiss → secret is gone for good.
    await user.click(within(reveal).getByRole("button", { name: /done/i }));
    await waitFor(() =>
      expect(screen.queryByTestId("token-reveal")).toBeNull(),
    );
  });

  it("revokes a token", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = pathOf(input);
        if (path === "/api/v1/auth/tokens/scopes") {
          return jsonResponse({ scopes: ["signals:read"] });
        }
        if (path === "/api/v1/auth/tokens/tok-1" && init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        if (path === "/api/v1/auth/tokens") {
          return jsonResponse({ items: [makeToken({})] });
        }
        return jsonResponse({ items: [] });
      });

    const user = userEvent.setup();
    render(<TokenManager kind="personal" />, { wrapper });

    await screen.findByTestId("token-list");
    await user.click(screen.getByRole("button", { name: /revoke/i }));

    await waitFor(() => {
      const del = fetchSpy.mock.calls.find(
        (c) =>
          pathOf(c[0]) === "/api/v1/auth/tokens/tok-1" &&
          c[1]?.method === "DELETE",
      );
      expect(del).toBeDefined();
    });
  });
});
