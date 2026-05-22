// @vitest-environment jsdom
// C3 — Entity directory: renders list items + filter interaction.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { EntityDirectory } from "@/app/entities/entity-directory";
import type { EntityPage, EntityRead } from "@/lib/entities-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function makeEntity(over: Partial<EntityRead>): EntityRead {
  return {
    id: "00000000-0000-7000-8000-000000000001",
    type: "school_district",
    status: "active",
    name: "Northshore School District",
    short_name: "Northshore SD",
    country: "US",
    state: "WA",
    region: null,
    kind_id: null,
    geo_id: null,
    parent_id: null,
    nces_leaid: null,
    ipeds_unitid: null,
    census_gid: null,
    population: null,
    enrollment: 12011,
    annual_budget_usd: 312_000_000,
    primary_website: "https://nsd.org",
    procurement_portal_url: null,
    board_meeting_cadence: null,
    attributes: {},
    source_urls: [],
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...over,
  };
}

function jsonEntityPage(items: EntityRead[], nextCursor: string | null = null): Response {
  const body: EntityPage = { items, next_cursor: nextCursor };
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    jsonEntityPage([
      makeEntity({
        id: "entity-1",
        name: "Northshore School District",
        state: "WA",
      }),
      makeEntity({
        id: "entity-2",
        name: "Plano ISD",
        state: "TX",
        enrollment: 55000,
      }),
    ]),
  );
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EntityDirectory", () => {
  it("renders a list of entities from the API", async () => {
    render(<EntityDirectory />, { wrapper });

    expect(await screen.findByText("Northshore School District")).toBeTruthy();
    expect(screen.getByText("Plano ISD")).toBeTruthy();
  });

  it("shows entity metadata (state, enrollment)", async () => {
    render(<EntityDirectory />, { wrapper });

    await screen.findByText("Northshore School District");
    // State code should appear in at least one card
    const waItems = screen.getAllByText(/WA/);
    expect(waItems.length).toBeGreaterThan(0);
    // Enrollment
    expect(screen.getByText(/12,011 enrolled/)).toBeTruthy();
  });

  it("each entity name links to the entity profile page", async () => {
    render(<EntityDirectory />, { wrapper });

    const link = await screen.findByRole("link", {
      name: "Northshore School District",
    });
    expect(link.getAttribute("href")).toBe("/entities/entity-1");
  });

  it("shows a count line after loading", async () => {
    render(<EntityDirectory />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText(/Showing 2 entities/)).toBeTruthy(),
    );
  });

  it("typing in the search box updates the filter state", async () => {
    const user = userEvent.setup();
    render(<EntityDirectory />, { wrapper });

    // Wait for initial load
    await screen.findByText("Northshore School District");

    const searchInput = screen.getByRole("searchbox", {
      name: /search entities by name/i,
    });
    await user.type(searchInput, "Plano");

    // The input should reflect the typed value
    expect((searchInput as HTMLInputElement).value).toBe("Plano");
  });

  it("selecting a type filter triggers a new API request", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonEntityPage([]));
    const user = userEvent.setup();
    render(<EntityDirectory />, { wrapper });

    const typeSelect = await screen.findByRole("combobox", {
      name: /filter by entity type/i,
    });
    await user.selectOptions(typeSelect, "school_district");

    await waitFor(() => {
      const calls = fetchSpy.mock.calls;
      const hasTypeParam = calls.some((c) =>
        String(c[0]).includes("type=school_district"),
      );
      expect(hasTypeParam).toBe(true);
    });
  });

  it("shows an error alert when the API returns an error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Internal Server Error",
          status: 500,
          detail: "Something went wrong",
        }),
        {
          status: 500,
          headers: { "Content-Type": "application/problem+json" },
        },
      ),
    );

    render(<EntityDirectory />, { wrapper });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Something went wrong");
  });

  it("shows a Load more button when next_cursor is set", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonEntityPage([makeEntity({ id: "entity-1", name: "Northshore SD" })], "cursor-abc"),
    );

    render(<EntityDirectory />, { wrapper });

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /load more/i }),
      ).toBeTruthy(),
    );
  });
});
