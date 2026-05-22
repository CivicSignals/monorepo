// @vitest-environment jsdom
// K5 — PushRecovery: renders failed pushes with inline diagnosis, retry triggers
// the mutation (POST /push-log/{id}/retry), success/error states render, the
// detail expander shows the push-log entry, and reconnect-needed failures steer
// the operator. Tests the component with fetch mocks routed by URL path.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { PushRecovery } from "../push-recovery";
import { useSessionStore } from "@/store/session";

const WORKSPACE_ID = "ws-k5-test";
const TRANSIENT_ID = "pl-transient";
const AUTH_ID = "pl-auth";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function transientFailure() {
  return {
    id: TRANSIENT_ID,
    connection_id: "conn-1",
    signal_id: "sig-1",
    pipeline_item_id: null,
    target: "salesforce.Opportunity",
    status: "failed",
    external_id: null,
    error: { code: "transient", message: "503 from provider" },
    attempt_count: 2,
    retry_at: null,
    attempted_at: "2026-05-20T00:00:00Z",
    created_at: "2026-05-20T00:00:00Z",
    diagnosis: {
      code: "transient",
      cause: "The provider had a transient error (5xx or network failure).",
      recommended_action: "Retry; it usually resolves on its own.",
      retryable: true,
      needs_reauth: false,
    },
  };
}

function authFailure() {
  return {
    id: AUTH_ID,
    connection_id: "conn-1",
    signal_id: "sig-2",
    pipeline_item_id: null,
    target: "salesforce.Opportunity",
    status: "dead_letter",
    external_id: null,
    error: { code: "auth", message: "401 invalid token" },
    attempt_count: 3,
    retry_at: null,
    attempted_at: "2026-05-20T00:00:00Z",
    created_at: "2026-05-20T00:00:00Z",
    diagnosis: {
      code: "auth",
      cause: "The connection's credentials are invalid or expired.",
      recommended_action: "Reconnect the integration, then retry.",
      retryable: true,
      needs_reauth: true,
    },
  };
}

function setupFetchMocks(
  opts: {
    failures?: object[];
    retryResponse?: object;
    retryStatus?: number;
  } = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname;
    const method = (init?.method ?? "GET").toUpperCase();

    if (path.endsWith("/retry") && method === "POST") {
      return jsonResponse(
        opts.retryResponse ?? {
          push_log: {
            id: "pl-retry-new",
            connection_id: "conn-1",
            signal_id: "sig-1",
            pipeline_item_id: null,
            target: "salesforce.Opportunity",
            status: "success",
            external_id: "sf-001",
            error: null,
            attempt_count: 1,
            retry_at: null,
            attempted_at: "2026-05-21T00:00:00Z",
            created_at: "2026-05-21T00:00:00Z",
          },
        },
        opts.retryStatus ?? 200,
      );
    }
    if (path.endsWith("/push-log/failures")) {
      return jsonResponse({
        data: opts.failures ?? [transientFailure()],
        next_cursor: null,
      });
    }
    return new Response(
      JSON.stringify({ status: 404, title: "Not found", type: "about:blank" }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    );
  });
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ accessToken: "jwt-test", user: null });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSessionStore.setState({ accessToken: null, user: null });
});

describe("PushRecovery", () => {
  it("shows an empty state when there are no failures", async () => {
    setupFetchMocks({ failures: [] });
    render(<PushRecovery workspaceId={WORKSPACE_ID} />, { wrapper });
    await waitFor(() => {
      expect(screen.queryByTestId("no-push-failures")).not.toBeNull();
    });
  });

  it("renders a failure with its inline diagnosis", async () => {
    setupFetchMocks();
    render(<PushRecovery workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId(`push-failure-${TRANSIENT_ID}`)).not.toBeNull();
    });
    const cause = screen.getByTestId("push-failure-cause");
    expect(cause.textContent).toMatch(/transient error/i);
    expect(screen.getByTestId("push-failure-status").textContent).toContain(
      "failed",
    );
  });

  it("triggers the retry mutation and shows success", async () => {
    const fetchSpy = setupFetchMocks();
    const user = userEvent.setup();
    render(<PushRecovery workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(
        screen.queryByTestId(`retry-push-btn-${TRANSIENT_ID}`),
      ).not.toBeNull();
    });
    await user.click(screen.getByTestId(`retry-push-btn-${TRANSIENT_ID}`));

    await waitFor(() => {
      const retryCall = fetchSpy.mock.calls.find(
        ([i, init]) =>
          String(i).includes(`/push-log/${TRANSIENT_ID}/retry`) &&
          (init?.method ?? "GET").toUpperCase() === "POST",
      );
      expect(retryCall).toBeTruthy();
    });
    await waitFor(() => {
      expect(screen.queryByRole("status")?.textContent).toMatch(/succeeded/i);
    });
  });

  it("surfaces a retry error", async () => {
    setupFetchMocks({ retryStatus: 409, retryResponse: { code: "not_retryable" } });
    const user = userEvent.setup();
    render(<PushRecovery workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(
        screen.queryByTestId(`retry-push-btn-${TRANSIENT_ID}`),
      ).not.toBeNull();
    });
    await user.click(screen.getByTestId(`retry-push-btn-${TRANSIENT_ID}`));

    await waitFor(() => {
      expect(screen.queryByRole("alert")?.textContent).toMatch(/could not retry/i);
    });
  });

  it("expands the push-log entry detail", async () => {
    setupFetchMocks();
    const user = userEvent.setup();
    render(<PushRecovery workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId(`toggle-detail-${TRANSIENT_ID}`)).not.toBeNull();
    });
    expect(
      screen.queryByTestId(`push-failure-detail-${TRANSIENT_ID}`),
    ).toBeNull();
    await user.click(screen.getByTestId(`toggle-detail-${TRANSIENT_ID}`));
    expect(
      screen.queryByTestId(`push-failure-detail-${TRANSIENT_ID}`),
    ).not.toBeNull();
  });

  it("flags reconnect-needed failures", async () => {
    setupFetchMocks({ failures: [authFailure()] });
    render(<PushRecovery workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId(`push-failure-${AUTH_ID}`)).not.toBeNull();
    });
    expect(screen.queryByRole("alert")?.textContent).toMatch(/reconnected/i);
  });
});
