// @vitest-environment jsdom
// B5 — Render + switch + create test for the workspace switcher.

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
import { WorkspaceSwitcher } from "../workspace-switcher";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type { Workspace } from "@/lib/workspaces-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeWorkspace(over: Partial<Workspace>): Workspace {
  return {
    id: "00000000-0000-7000-8000-000000000001",
    name: "Acme SLED",
    slug: "acme-sled",
    organization_id: "00000000-0000-7000-8000-0000000000aa",
    owner_id: "00000000-0000-7000-8000-0000000000bb",
    country_default: "US",
    role: "owner",
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...over,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ accessToken: "jwt-access", user: null });
  useUiStore.setState({ activeWorkspaceId: null });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSessionStore.setState({ accessToken: null, user: null });
  useUiStore.setState({ activeWorkspaceId: null });
});

describe("WorkspaceSwitcher", () => {
  it("renders the workspaces the user belongs to", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        items: [
          makeWorkspace({ id: "ws-1", name: "Acme SLED" }),
          makeWorkspace({ id: "ws-2", name: "Beta Team", slug: "beta" }),
        ],
        next_cursor: null,
      }),
    );

    render(<WorkspaceSwitcher />, { wrapper });

    const select = await screen.findByRole("combobox", {
      name: /active workspace/i,
    });
    const options = within(select).getAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "Acme SLED",
      "Beta Team",
    ]);
  });

  it("switches the active workspace via the switch endpoint", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (
          url.endsWith("/workspaces") &&
          (!init || init.method === undefined)
        ) {
          return jsonResponse({
            items: [
              makeWorkspace({ id: "ws-1", name: "Acme SLED" }),
              makeWorkspace({ id: "ws-2", name: "Beta Team", slug: "beta" }),
            ],
            next_cursor: null,
          });
        }
        if (url.includes("/workspaces/ws-2/switch")) {
          return jsonResponse(makeWorkspace({ id: "ws-2", name: "Beta Team" }));
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<WorkspaceSwitcher />, { wrapper });

    const select = await screen.findByRole("combobox", {
      name: /active workspace/i,
    });
    await user.selectOptions(select, "ws-2");

    await waitFor(() =>
      expect(useUiStore.getState().activeWorkspaceId).toBe("ws-2"),
    );
    const switchCall = fetchSpy.mock.calls.find((c) =>
      String(c[0]).includes("/workspaces/ws-2/switch"),
    );
    expect(switchCall).toBeDefined();
    expect(switchCall?.[1]?.method).toBe("POST");
  });

  it("creates a new workspace and makes it active", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/workspaces") && init?.method === "POST") {
          return jsonResponse(
            makeWorkspace({ id: "ws-new", name: "Fresh WS", slug: "fresh-ws" }),
            201,
          );
        }
        if (url.endsWith("/workspaces")) {
          return jsonResponse({ items: [], next_cursor: null });
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<WorkspaceSwitcher />, { wrapper });

    await user.click(
      await screen.findByRole("button", { name: /new workspace/i }),
    );
    await user.type(screen.getByLabelText(/new workspace name/i), "Fresh WS");
    await user.click(screen.getByRole("button", { name: /^create$/i }));

    await waitFor(() =>
      expect(useUiStore.getState().activeWorkspaceId).toBe("ws-new"),
    );
    const createCall = fetchSpy.mock.calls.find(
      (c) => String(c[0]).endsWith("/workspaces") && c[1]?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(String(createCall?.[1]?.body)).toContain("Fresh WS");
  });
});
