// @vitest-environment jsdom
// H1 — SavedSearchManager: list (own + shared), create, rename/re-filter, delete.

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
import { SavedSearchManager } from "../saved-search-manager";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type { SavedSearchOut } from "@/lib/searches-api";

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

function makeSearch(over: Partial<SavedSearchOut>): SavedSearchOut {
  return {
    id: "search-1",
    workspace_id: "ws-1",
    created_by: "user-1",
    name: "Hot RFPs",
    filters: { signal_type: "rfp_posted", statuses: ["new"], min_score: 60 },
    is_shared: false,
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
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
  useUiStore.setState({ activeWorkspaceId: "ws-1" });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSessionStore.setState({ accessToken: null, user: null });
  useUiStore.setState({ activeWorkspaceId: null });
});

describe("SavedSearchManager", () => {
  it("lists own and shared searches", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (pathOf(input) === "/api/v1/searches") {
        return jsonResponse({
          items: [
            makeSearch({ id: "s1", name: "My private" }),
            makeSearch({ id: "s2", name: "Team shared", is_shared: true }),
          ],
          next_cursor: null,
        });
      }
      return jsonResponse({ items: [], next_cursor: null });
    });

    render(<SavedSearchManager />, { wrapper });

    const list = await screen.findByTestId("saved-search-list");
    expect(within(list).queryByText("My private")).not.toBeNull();
    expect(within(list).queryByText("Team shared")).not.toBeNull();
    // The shared badge is rendered for shared searches.
    expect(within(list).queryByText("shared")).not.toBeNull();
  });

  it("creates a saved search with the chosen filters", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = pathOf(input);
        if (path === "/api/v1/searches" && init?.method === "POST") {
          return jsonResponse(makeSearch({ id: "new", name: "Big deals" }), 201);
        }
        if (path === "/api/v1/searches") {
          return jsonResponse({ items: [], next_cursor: null });
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<SavedSearchManager />, { wrapper });

    await screen.findByTestId("saved-search-manager");
    await user.type(screen.getByLabelText(/^name$/i), "Big deals");
    await user.selectOptions(
      screen.getByLabelText(/signal type/i),
      "grant_awarded",
    );
    await user.click(screen.getByRole("button", { name: /save search/i }));

    await waitFor(() => {
      const post = fetchSpy.mock.calls.find(
        (c) => pathOf(c[0]) === "/api/v1/searches" && c[1]?.method === "POST",
      );
      expect(post).toBeDefined();
    });
    const post = fetchSpy.mock.calls.find(
      (c) => pathOf(c[0]) === "/api/v1/searches" && c[1]?.method === "POST",
    );
    const body = JSON.parse(String(post?.[1]?.body)) as {
      name: string;
      filters: { signal_type?: string };
    };
    expect(body.name).toBe("Big deals");
    expect(body.filters.signal_type).toBe("grant_awarded");
  });

  it("renames a saved search via the edit form", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = pathOf(input);
        if (path === "/api/v1/searches/s1" && init?.method === "PATCH") {
          return jsonResponse(makeSearch({ id: "s1", name: "Renamed" }));
        }
        if (path === "/api/v1/searches") {
          return jsonResponse({
            items: [makeSearch({ id: "s1", name: "Original" })],
            next_cursor: null,
          });
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<SavedSearchManager />, { wrapper });

    await screen.findByTestId("saved-search-list");
    await user.click(screen.getByRole("button", { name: /edit/i }));

    const nameInput = screen.getByLabelText(/^name$/i, {
      selector: "#edit-s1-name",
    });
    await user.clear(nameInput);
    await user.type(nameInput, "Renamed");
    await user.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() => {
      const patch = fetchSpy.mock.calls.find(
        (c) =>
          pathOf(c[0]) === "/api/v1/searches/s1" && c[1]?.method === "PATCH",
      );
      expect(patch).toBeDefined();
    });
    const patch = fetchSpy.mock.calls.find(
      (c) => pathOf(c[0]) === "/api/v1/searches/s1" && c[1]?.method === "PATCH",
    );
    expect(String(patch?.[1]?.body)).toContain("Renamed");
  });

  it("deletes a saved search", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = pathOf(input);
        if (path === "/api/v1/searches/s1" && init?.method === "DELETE") {
          return new Response(null, { status: 204 });
        }
        if (path === "/api/v1/searches") {
          return jsonResponse({
            items: [makeSearch({ id: "s1" })],
            next_cursor: null,
          });
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    const user = userEvent.setup();
    render(<SavedSearchManager />, { wrapper });

    await screen.findByTestId("saved-search-list");
    await user.click(screen.getByRole("button", { name: /delete/i }));

    await waitFor(() => {
      const del = fetchSpy.mock.calls.find(
        (c) =>
          pathOf(c[0]) === "/api/v1/searches/s1" && c[1]?.method === "DELETE",
      );
      expect(del).toBeDefined();
    });
  });

  it("requires a name on create", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse({ items: [], next_cursor: null }),
    );

    const user = userEvent.setup();
    render(<SavedSearchManager />, { wrapper });

    await screen.findByTestId("saved-search-manager");
    await user.click(screen.getByRole("button", { name: /save search/i }));

    expect(await screen.findByText(/give your search a name/i)).toBeDefined();
  });
});
