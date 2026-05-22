// @vitest-environment jsdom
// K2 — SalesforceSettings: connect button, connection panel, object dropdown
// from discovery, field-mapping rows from discovery, and save mapping. Tests the
// component with fetch mocks routed by URL path.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { SalesforceSettings } from "../salesforce-settings";
import { useSessionStore } from "@/store/session";

const WORKSPACE_ID = "ws-k2-test";
const CONNECTION_ID = "conn-1";

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

function healthyConnection() {
  return {
    id: CONNECTION_ID,
    provider: "salesforce",
    name: "Acme prod",
    status: "healthy",
    scopes: ["api"],
    default_targets: ["Opportunity"],
    provider_account: { instance_url: "https://acme.my.salesforce.com" },
    connected_at: "2026-05-01T00:00:00Z",
    last_push_at: null,
    created_at: "2026-05-01T00:00:00Z",
  };
}

function setupFetchMocks(
  opts: {
    connections?: object[];
    objects?: object[];
    fields?: object[];
    saveResponse?: object;
    connectResponse?: object;
  } = {},
) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(String(input), "http://localhost");
    const path = url.pathname;
    const method = (init?.method ?? "GET").toUpperCase();

    if (path.endsWith("/discover/objects")) {
      return jsonResponse({
        data: opts.objects ?? [
          { name: "Opportunity", label: "Opportunity", custom: false },
          { name: "Civic_Signal__c", label: "Civic Signal", custom: true },
        ],
      });
    }
    if (path.endsWith("/discover/fields")) {
      return jsonResponse({
        object: "Opportunity",
        data: opts.fields ?? [
          {
            name: "Name",
            label: "Name",
            type: "string",
            required: true,
            createable: true,
            updateable: true,
          },
          {
            name: "Amount",
            label: "Amount",
            type: "currency",
            required: false,
            createable: true,
            updateable: true,
          },
        ],
      });
    }
    if (path.endsWith("/field-mappings") && method === "PUT") {
      return jsonResponse(
        opts.saveResponse ?? {
          id: "fm-1",
          connection_id: CONNECTION_ID,
          target_object: "Opportunity",
          field_map: { Name: "signal.title" },
          constants: {},
          created_at: "2026-05-01T00:00:00Z",
          updated_at: "2026-05-01T00:00:00Z",
        },
      );
    }
    if (path.endsWith("/field-mappings")) {
      return jsonResponse({ data: [] });
    }
    if (path.endsWith("/integrations/connections") && method === "POST") {
      return jsonResponse(
        opts.connectResponse ?? {
          id: "conn-new",
          provider: "salesforce",
          status: "pending_oauth",
          redirect_url: "https://login.salesforce.com/authorize?x=1",
        },
        201,
      );
    }
    if (path.endsWith("/integrations/connections")) {
      return jsonResponse({ data: opts.connections ?? [healthyConnection()] });
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

describe("SalesforceSettings", () => {
  it("renders the connect card", async () => {
    setupFetchMocks({ connections: [] });
    render(<SalesforceSettings workspaceId={WORKSPACE_ID} />, { wrapper });
    await waitFor(() => {
      expect(screen.queryByTestId("connect-card")).not.toBeNull();
    });
    expect(screen.queryByTestId("connect-salesforce-btn")).not.toBeNull();
  });

  it("shows an empty state when there are no connections", async () => {
    setupFetchMocks({ connections: [] });
    render(<SalesforceSettings workspaceId={WORKSPACE_ID} />, { wrapper });
    await waitFor(() => {
      expect(screen.queryByTestId("no-connections")).not.toBeNull();
    });
  });

  it("renders a connection panel with the object dropdown from discovery", async () => {
    setupFetchMocks();
    render(<SalesforceSettings workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId(`connection-${CONNECTION_ID}`)).not.toBeNull();
    });

    const select = (await screen.findByTestId(
      "object-select",
    )) as HTMLSelectElement;
    await waitFor(() => {
      const opts = Array.from(select.options).map((o) => o.value);
      expect(opts).toContain("Civic_Signal__c");
      expect(opts).toContain("Opportunity");
    });
  });

  it("renders field-mapping rows from discovery", async () => {
    setupFetchMocks();
    render(<SalesforceSettings workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId("field-mapping-editor")).not.toBeNull();
    });
    await waitFor(() => {
      expect(screen.queryByTestId("field-row-Name")).not.toBeNull();
      expect(screen.queryByTestId("field-row-Amount")).not.toBeNull();
    });
  });

  it("saves a field mapping when the row is set and Save is clicked", async () => {
    const fetchSpy = setupFetchMocks();
    const user = userEvent.setup();
    render(<SalesforceSettings workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId("map-select-Name")).not.toBeNull();
    });

    const mapSelect = screen.getByTestId("map-select-Name") as HTMLSelectElement;
    await user.selectOptions(mapSelect, "signal.title");

    const saveBtn = screen.getByTestId("save-mapping-btn") as HTMLButtonElement;
    await user.click(saveBtn);

    await waitFor(() => {
      const putCall = fetchSpy.mock.calls.find(
        ([input, init]) =>
          String(input).includes("/field-mappings") &&
          (init?.method ?? "GET").toUpperCase() === "PUT",
      );
      expect(putCall).toBeTruthy();
      const body = JSON.parse(String(putCall?.[1]?.body));
      expect(body.target_object).toBe("Opportunity");
      expect(body.field_map.Name).toBe("signal.title");
    });

    await waitFor(() => {
      expect(screen.queryByRole("status")?.textContent).toMatch(/saved/i);
    });
  });

  it("starts the OAuth flow on connect", async () => {
    const fetchSpy = setupFetchMocks({ connections: [] });
    const user = userEvent.setup();

    const locationMock = { href: "http://localhost/settings/integrations" };
    Object.defineProperty(window, "location", {
      value: locationMock,
      writable: true,
    });

    render(<SalesforceSettings workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId("connect-salesforce-btn")).not.toBeNull();
    });
    await user.click(screen.getByTestId("connect-salesforce-btn"));

    await waitFor(() => {
      const postCall = fetchSpy.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/integrations/connections") &&
          (init?.method ?? "GET").toUpperCase() === "POST",
      );
      expect(postCall).toBeTruthy();
    });
    await waitFor(() => {
      expect(locationMock.href).toContain("login.salesforce.com");
    });
  });
});
