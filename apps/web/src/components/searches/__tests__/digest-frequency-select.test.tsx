// @vitest-environment jsdom
// H3 — DigestFrequencySelect: reads the current frequency, sets a new one via PUT.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { DigestFrequencySelect } from "../digest-frequency-select";
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

const DIGEST_PATH = "/api/v1/notifications/digests/s1";

function makeDigest(frequency: "off" | "daily" | "weekly") {
  return {
    id: "d1",
    saved_search_id: "s1",
    workspace_id: "ws-1",
    user_id: "user-1",
    frequency,
    send_hour: 8,
    weekday: 0,
    timezone: "UTC",
    last_sent_at: null,
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
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

describe("DigestFrequencySelect", () => {
  it("renders 'off' when no digest is configured (404 -> null)", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (pathOf(input) === DIGEST_PATH) {
        return jsonResponse(
          { type: "about:blank", title: "Not found", status: 404 },
          404,
        );
      }
      return jsonResponse({});
    });

    render(<DigestFrequencySelect savedSearchId="s1" />, { wrapper });

    const select = (await screen.findByLabelText(
      /digest frequency/i,
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("off"));
  });

  it("reflects the configured frequency", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      if (pathOf(input) === DIGEST_PATH) {
        return jsonResponse(makeDigest("weekly"));
      }
      return jsonResponse({});
    });

    render(<DigestFrequencySelect savedSearchId="s1" />, { wrapper });

    const select = (await screen.findByLabelText(
      /digest frequency/i,
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("weekly"));
  });

  it("PUTs the chosen frequency when changed", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const path = pathOf(input);
        if (path === DIGEST_PATH && init?.method === "PUT") {
          return jsonResponse(makeDigest("daily"));
        }
        if (path === DIGEST_PATH) {
          // initial GET -> not configured
          return jsonResponse(
            { type: "about:blank", title: "Not found", status: 404 },
            404,
          );
        }
        return jsonResponse({});
      });

    const user = userEvent.setup();
    render(<DigestFrequencySelect savedSearchId="s1" />, { wrapper });

    const select = (await screen.findByLabelText(
      /digest frequency/i,
    )) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("off"));

    await user.selectOptions(select, "daily");

    await waitFor(() => {
      const put = fetchSpy.mock.calls.find(
        (c) => pathOf(c[0]) === DIGEST_PATH && c[1]?.method === "PUT",
      );
      expect(put).toBeDefined();
    });
    const put = fetchSpy.mock.calls.find(
      (c) => pathOf(c[0]) === DIGEST_PATH && c[1]?.method === "PUT",
    );
    const body = JSON.parse(String(put?.[1]?.body)) as {
      frequency: string;
      timezone?: string;
    };
    expect(body.frequency).toBe("daily");
    expect(typeof body.timezone).toBe("string");
    // The PUT response settles the select to the server value.
    await waitFor(() => expect(select.value).toBe("daily"));
  });
});
