// @vitest-environment jsdom
// K3 — HubspotSettings: connect button, connection panel, object dropdown from
// discovery (Deal + custom object), property-mapping rows from discovery
// (including a custom property), and save mapping. Mirrors the K2 Salesforce
// component test; fetch is mocked and routed by URL path (no live HubSpot).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { HubspotSettings } from "../hubspot-settings";
import { useSessionStore } from "@/store/session";

const WORKSPACE_ID = "ws-k3-test";
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
    provider: "hubspot",
    name: "Acme HubSpot",
    status: "healthy",
    scopes: ["crm.objects.deals.write"],
    default_targets: ["deals"],
    provider_account: { hub_id: "12345678" },
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
          { name: "deals", label: "Deal", custom: false },
          { name: "p12345_civic_signal", label: "Civic Signals", custom: true },
        ],
      });
    }
    if (path.endsWith("/discover/fields")) {
      return jsonResponse({
        object: "deals",
        data: opts.fields ?? [
          {
            name: "dealname",
            label: "Deal Name",
            type: "string",
            required: true,
            createable: true,
            updateable: true,
          },
          // A custom property surfaced by discovery.
          {
            name: "civic_signal_url",
            label: "Civic Signal URL",
            type: "string",
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
          target_object: "deals",
          field_map: { dealname: "signal.title" },
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
          provider: "hubspot",
          status: "pending_oauth",
          redirect_url: "https://app.hubspot.com/oauth/authorize?x=1",
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

describe("HubspotSettings", () => {
  it("renders the connect card", async () => {
    setupFetchMocks({ connections: [] });
    render(<HubspotSettings workspaceId={WORKSPACE_ID} />, { wrapper });
    await waitFor(() => {
      expect(screen.queryByTestId("connect-card")).not.toBeNull();
    });
    expect(screen.queryByTestId("connect-hubspot-btn")).not.toBeNull();
  });

  it("shows an empty state when there are no connections", async () => {
    setupFetchMocks({ connections: [] });
    render(<HubspotSettings workspaceId={WORKSPACE_ID} />, { wrapper });
    await waitFor(() => {
      expect(screen.queryByTestId("no-connections")).not.toBeNull();
    });
  });

  it("renders a connection panel with the object dropdown from discovery", async () => {
    setupFetchMocks();
    render(<HubspotSettings workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId(`connection-${CONNECTION_ID}`)).not.toBeNull();
    });

    const select = (await screen.findByTestId(
      "object-select",
    )) as HTMLSelectElement;
    await waitFor(() => {
      const opts = Array.from(select.options).map((o) => o.value);
      expect(opts).toContain("deals");
      expect(opts).toContain("p12345_civic_signal");
    });
  });

  it("renders property-mapping rows from discovery (incl. custom property)", async () => {
    setupFetchMocks();
    render(<HubspotSettings workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId("field-mapping-editor")).not.toBeNull();
    });
    await waitFor(() => {
      expect(screen.queryByTestId("field-row-dealname")).not.toBeNull();
      expect(screen.queryByTestId("field-row-civic_signal_url")).not.toBeNull();
    });
  });

  it("saves a property mapping when the row is set and Save is clicked", async () => {
    const fetchSpy = setupFetchMocks();
    const user = userEvent.setup();
    render(<HubspotSettings workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId("map-select-dealname")).not.toBeNull();
    });

    const mapSelect = screen.getByTestId(
      "map-select-dealname",
    ) as HTMLSelectElement;
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
      expect(body.target_object).toBe("deals");
      expect(body.field_map.dealname).toBe("signal.title");
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

    render(<HubspotSettings workspaceId={WORKSPACE_ID} />, { wrapper });

    await waitFor(() => {
      expect(screen.queryByTestId("connect-hubspot-btn")).not.toBeNull();
    });
    await user.click(screen.getByTestId("connect-hubspot-btn"));

    await waitFor(() => {
      const postCall = fetchSpy.mock.calls.find(
        ([input, init]) =>
          String(input).endsWith("/integrations/connections") &&
          (init?.method ?? "GET").toUpperCase() === "POST",
      );
      expect(postCall).toBeTruthy();
      const body = JSON.parse(String(postCall?.[1]?.body));
      expect(body.provider).toBe("hubspot");
    });
    await waitFor(() => {
      expect(locationMock.href).toContain("app.hubspot.com");
    });
  });
});
