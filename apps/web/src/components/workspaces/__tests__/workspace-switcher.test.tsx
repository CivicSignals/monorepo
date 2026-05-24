// @vitest-environment jsdom
// B5 — Render + switch + create test for the workspace switcher dropdown.

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

// Match on the URL pathname so query params (?limit=, ?cursor=) don't break
// routing in the mock.
function pathOf(input: RequestInfo | URL): string {
  return new URL(String(input), "http://localhost").pathname;
}

// Open the dropdown by clicking the trigger.
async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTestId("workspace-trigger"));
  return screen.getByRole("menu", { name: /workspaces/i });
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
  it("lists the workspaces the user belongs to in the menu", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({
        items: [
          makeWorkspace({ id: "ws-1", name: "Acme SLED" }),
          makeWorkspace({ id: "ws-2", name: "Beta Team", slug: "beta" }),
        ],
        next_cursor: null,
      }),
    );

    const user = userEvent.setup();
    render(<WorkspaceSwitcher />, { wrapper });

    const menu = await openMenu(user);
    const items = within(menu).getAllByRole("menuitemradio");
    expect(items.map((i) => i.textContent?.trim())).toEqual([
      "Acme SLED",
      "Beta Team",
    ]);
  });

  it("explains what a workspace is", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ items: [makeWorkspace({ id: "ws-1" })], next_cursor: null }),
    );

    const user = userEvent.setup();
    render(<WorkspaceSwitcher />, { wrapper });

    const menu = await openMenu(user);
    expect(within(menu).getByText(/separate space for one team or client/i)).toBeTruthy();
  });

  it("switches the active workspace via the switch endpoint", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path === "/api/v1/workspaces") {
          return jsonResponse({
            items: [
              makeWorkspace({ id: "ws-1", name: "Acme SLED" }),
              makeWorkspace({ id: "ws-2", name: "Beta Team", slug: "beta" }),
            ],
            next_cursor: null,
          });
        }
        if (path === "/api/v1/workspaces/ws-2/switch") {
          return jsonResponse(makeWorkspace({ id: "ws-2", name: "Beta Team" }));
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<WorkspaceSwitcher />, { wrapper });

    const menu = await openMenu(user);
    await user.click(within(menu).getByRole("menuitemradio", { name: "Beta Team" }));

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
        const path = pathOf(input);
        if (path === "/api/v1/workspaces" && init?.method === "POST") {
          return jsonResponse(
            makeWorkspace({ id: "ws-new", name: "Fresh WS", slug: "fresh-ws" }),
            201,
          );
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<WorkspaceSwitcher />, { wrapper });

    await openMenu(user);
    await user.type(screen.getByLabelText(/new workspace name/i), "Fresh WS");
    await user.click(screen.getByRole("button", { name: /create new/i }));

    await waitFor(() =>
      expect(useUiStore.getState().activeWorkspaceId).toBe("ws-new"),
    );
    const createCall = fetchSpy.mock.calls.find(
      (c) => pathOf(c[0]) === "/api/v1/workspaces" && c[1]?.method === "POST",
    );
    expect(createCall).toBeDefined();
    expect(String(createCall?.[1]?.body)).toContain("Fresh WS");
  });

  it("shows an empty-state prompt when the user has no workspaces", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ items: [], next_cursor: null }),
    );

    const user = userEvent.setup();
    render(<WorkspaceSwitcher />, { wrapper });

    const menu = await openMenu(user);
    expect(within(menu).getByText(/no workspace yet/i)).toBeTruthy();
    // The create field is still reachable — the empty state is not a dead end.
    expect(within(menu).getByLabelText(/new workspace name/i)).toBeTruthy();
  });
});
