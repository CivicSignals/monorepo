// @vitest-environment jsdom
// C3 — Entity profile: renders entity fields + children list.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { EntityProfile } from "@/app/entities/[id]/entity-profile";
import type { EntityPage, EntityRead } from "@/lib/entities-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

const BASE_ENTITY: EntityRead = {
  id: "ent-abc",
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
  nces_leaid: "530462",
  ipeds_unitid: null,
  census_gid: null,
  population: null,
  enrollment: 12011,
  annual_budget_usd: 312_000_000,
  primary_website: "https://nsd.org",
  procurement_portal_url: "https://bonfirehub.com/portal/?tab=openOpps&portalID=NSD123",
  board_meeting_cadence: "2nd & 4th Tuesday",
  attributes: {},
  source_urls: ["https://nsd.org/about", "https://data.nces.gov/123"],
  created_at: "2026-05-22T00:00:00Z",
  updated_at: "2026-05-22T00:00:00Z",
};

const CHILD_ENTITY: EntityRead = {
  ...BASE_ENTITY,
  id: "ent-child-1",
  name: "Northshore High School",
  type: "school",
  parent_id: "ent-abc",
  enrollment: 2100,
  annual_budget_usd: null,
  primary_website: null,
  procurement_portal_url: null,
  nces_leaid: null,
  board_meeting_cadence: null,
  source_urls: [],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pathOf(input: RequestInfo | URL): string {
  return new URL(String(input), "http://localhost").pathname;
}

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = pathOf(input);
    if (path === "/api/v1/entities/ent-abc") {
      return jsonResponse(BASE_ENTITY);
    }
    if (path === "/api/v1/entities/ent-abc/children") {
      const page: EntityPage = {
        items: [CHILD_ENTITY],
        next_cursor: null,
      };
      return jsonResponse(page);
    }
    // Unknown paths — return empty page so infinite queries don't break
    return jsonResponse({ items: [], next_cursor: null });
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EntityProfile", () => {
  it("renders the entity name and type", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    expect(
      await screen.findByRole("heading", { name: "Northshore School District" }),
    ).toBeTruthy();
    // The type appears in the subtitle paragraph
    const allMatches = screen.getAllByText(/School District/);
    expect(allMatches.length).toBeGreaterThan(0);
  });

  it("renders the state in the subtitle", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    await screen.findByRole("heading", { name: "Northshore School District" });
    // Subtitle includes 'WA'
    expect(screen.getByText(/WA/)).toBeTruthy();
  });

  it("renders the status badge", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    await screen.findByRole("heading", { name: "Northshore School District" });
    expect(screen.getByText(/Active/i)).toBeTruthy();
  });

  it("renders enrollment and budget", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    await screen.findByText(/12,011 students/);
    // $312M budget
    expect(screen.getByText(/\$312M/)).toBeTruthy();
  });

  it("renders board meeting cadence", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    expect(await screen.findByText(/2nd & 4th Tuesday/)).toBeTruthy();
  });

  it("renders the primary website as a link", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    await screen.findByRole("heading", { name: "Northshore School District" });
    const websiteLink = screen.getByRole("link", { name: "https://nsd.org" });
    expect(websiteLink.getAttribute("href")).toBe("https://nsd.org");
    expect(websiteLink.getAttribute("target")).toBe("_blank");
  });

  it("renders the NCES LEAID", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    expect(await screen.findByText("530462")).toBeTruthy();
  });

  it("renders source URL citations", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    await screen.findByRole("heading", { name: /Source Citations/i });
    const citationLink = screen.getByRole("link", {
      name: "https://nsd.org/about",
    });
    expect(citationLink.getAttribute("href")).toBe("https://nsd.org/about");
  });

  it("renders the children section with child entities", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    expect(
      await screen.findByText("Northshore High School"),
    ).toBeTruthy();
  });

  it("child entities link to their own profile pages", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    const childLink = await screen.findByRole("link", {
      name: "Northshore High School",
    });
    expect(childLink.getAttribute("href")).toBe("/entities/ent-child-1");
  });

  it("renders the Contacts placeholder section with TODO C4 note", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    await screen.findByRole("heading", { name: /Contacts/i });
    expect(screen.getByText(/future release/i)).toBeTruthy();
  });

  it("renders 'Load more children' when next_cursor is set", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = pathOf(input);
      if (path === "/api/v1/entities/ent-abc") return jsonResponse(BASE_ENTITY);
      if (path === "/api/v1/entities/ent-abc/children") {
        return jsonResponse({ items: [CHILD_ENTITY], next_cursor: "cursor-xyz" });
      }
      return jsonResponse({ items: [], next_cursor: null });
    });

    render(<EntityProfile id="ent-abc" />, { wrapper });

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /load more children/i }),
      ).toBeTruthy(),
    );
  });

  it("shows not found state when entity is 404", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Entity not found",
          status: 404,
          detail: "No entity with id ent-missing.",
        }),
        {
          status: 404,
          headers: { "Content-Type": "application/problem+json" },
        },
      ),
    );

    render(<EntityProfile id="ent-missing" />, { wrapper });

    expect(
      await screen.findByText(/Entity not found\./i),
    ).toBeTruthy();
  });

  it("renders a back-to-directory link", async () => {
    render(<EntityProfile id="ent-abc" />, { wrapper });
    await screen.findByRole("heading", { name: "Northshore School District" });
    const backLink = screen.getByRole("link", { name: /Entity directory/i });
    expect(backLink.getAttribute("href")).toBe("/entities");
  });

  it("fetches entity and children using the correct API paths", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path === "/api/v1/entities/ent-abc") return jsonResponse(BASE_ENTITY);
        if (path === "/api/v1/entities/ent-abc/children") {
          return jsonResponse({ items: [], next_cursor: null });
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

    render(<EntityProfile id="ent-abc" />, { wrapper });
    await screen.findByRole("heading", { name: "Northshore School District" });

    const paths = fetchSpy.mock.calls.map((c) => pathOf(c[0]));
    expect(paths).toContain("/api/v1/entities/ent-abc");
    expect(paths).toContain("/api/v1/entities/ent-abc/children");
  });

  it("shows a Load more children button that fetches the next page", async () => {
    let callCount = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = pathOf(input);
      if (path === "/api/v1/entities/ent-abc") return jsonResponse(BASE_ENTITY);
      if (path === "/api/v1/entities/ent-abc/children") {
        callCount += 1;
        if (callCount === 1) {
          return jsonResponse({ items: [CHILD_ENTITY], next_cursor: "cursor-p2" });
        }
        return jsonResponse({ items: [], next_cursor: null });
      }
      return jsonResponse({ items: [], next_cursor: null });
    });

    const user = userEvent.setup();
    render(<EntityProfile id="ent-abc" />, { wrapper });

    const loadMoreBtn = await screen.findByRole("button", {
      name: /load more children/i,
    });
    await user.click(loadMoreBtn);

    await waitFor(() => expect(callCount).toBe(2));
  });
});
