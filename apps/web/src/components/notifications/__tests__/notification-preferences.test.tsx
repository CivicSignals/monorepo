// @vitest-environment jsdom
// H5 — NotificationPreferences: list the user's digest subscriptions and let them
// change the frequency / unsubscribe per saved search.

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
import { NotificationPreferences } from "../notification-preferences";
import type { DigestSubscriptionListItem } from "@/lib/digests-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

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

function makeSub(
  over: Partial<DigestSubscriptionListItem>,
): DigestSubscriptionListItem {
  return {
    id: "sub-1",
    saved_search_id: "search-1",
    workspace_id: "ws-1",
    user_id: "user-1",
    frequency: "daily",
    send_hour: 8,
    weekday: 0,
    timezone: "America/New_York",
    last_sent_at: null,
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    saved_search_name: "Hot RFPs",
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

describe("NotificationPreferences", () => {
  it("lists the user's digest subscriptions with names and frequency", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (pathOf(input) === "/api/v1/notifications/digests") {
        return jsonResponse({
          items: [
            makeSub({ id: "s1", saved_search_name: "Hot RFPs", frequency: "daily" }),
            makeSub({
              id: "s2",
              saved_search_id: "search-2",
              saved_search_name: "Big grants",
              frequency: "weekly",
            }),
          ],
        });
      }
      return jsonResponse({ items: [] });
    });

    render(<NotificationPreferences />, { wrapper });

    const list = await screen.findByTestId("digest-prefs-list");
    expect(within(list).queryByText("Hot RFPs")).not.toBeNull();
    expect(within(list).queryByText("Big grants")).not.toBeNull();
    // Each row shows its current frequency.
    expect(within(list).queryByText(/Currently: Daily/)).not.toBeNull();
    expect(within(list).queryByText(/Currently: Weekly/)).not.toBeNull();
  });

  it("renders an empty state with a link to saved searches", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse({ items: [] }),
    );

    render(<NotificationPreferences />, { wrapper });

    await screen.findByTestId("notification-preferences");
    expect(
      await screen.findByText(/no digest subscriptions yet/i),
    ).toBeDefined();
  });

  it("changes the frequency for a row (PUT) preserving the schedule", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = pathOf(input);
        if (
          path === "/api/v1/notifications/digests/search-1" &&
          init?.method === "PUT"
        ) {
          return jsonResponse(makeSub({ frequency: "weekly" }));
        }
        if (path === "/api/v1/notifications/digests") {
          return jsonResponse({
            items: [makeSub({ frequency: "daily" })],
          });
        }
        return jsonResponse({ items: [] });
      });

    const user = userEvent.setup();
    render(<NotificationPreferences />, { wrapper });

    await screen.findByTestId("digest-prefs-list");
    await user.selectOptions(
      screen.getByLabelText(/digest frequency for hot rfps/i),
      "weekly",
    );

    await waitFor(() => {
      const put = fetchSpy.mock.calls.find(
        (c) =>
          pathOf(c[0]) === "/api/v1/notifications/digests/search-1" &&
          c[1]?.method === "PUT",
      );
      expect(put).toBeDefined();
    });
    const put = fetchSpy.mock.calls.find(
      (c) =>
        pathOf(c[0]) === "/api/v1/notifications/digests/search-1" &&
        c[1]?.method === "PUT",
    );
    const body = JSON.parse(String(put?.[1]?.body)) as {
      frequency: string;
      send_hour: number;
      timezone: string;
    };
    expect(body.frequency).toBe("weekly");
    // The existing schedule fields are preserved (not reset to defaults).
    expect(body.send_hour).toBe(8);
    expect(body.timezone).toBe("America/New_York");
  });

  it("unsubscribes a row via the Unsubscribe button (PUT off)", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = pathOf(input);
        if (
          path === "/api/v1/notifications/digests/search-1" &&
          init?.method === "PUT"
        ) {
          return jsonResponse(makeSub({ frequency: "off" }));
        }
        if (path === "/api/v1/notifications/digests") {
          return jsonResponse({ items: [makeSub({ frequency: "daily" })] });
        }
        return jsonResponse({ items: [] });
      });

    const user = userEvent.setup();
    render(<NotificationPreferences />, { wrapper });

    await screen.findByTestId("digest-prefs-list");
    await user.click(screen.getByRole("button", { name: /unsubscribe/i }));

    await waitFor(() => {
      const put = fetchSpy.mock.calls.find(
        (c) =>
          pathOf(c[0]) === "/api/v1/notifications/digests/search-1" &&
          c[1]?.method === "PUT",
      );
      expect(put).toBeDefined();
    });
    const put = fetchSpy.mock.calls.find(
      (c) =>
        pathOf(c[0]) === "/api/v1/notifications/digests/search-1" &&
        c[1]?.method === "PUT",
    );
    expect(JSON.parse(String(put?.[1]?.body)).frequency).toBe("off");
  });
});
