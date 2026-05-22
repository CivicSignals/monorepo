// @vitest-environment jsdom
// C4 — Contacts list: renders names/titles, verified/stale badge, empty + error states.
// Mocks the contacts API client (GET /contacts?entity_id=…).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ContactList } from "@/components/contacts/contact-list";
import type { ContactPage, ContactRead } from "@/lib/contacts-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

// ---- Fixtures ----

const NOW_ISO = new Date().toISOString();
const OLD_ISO = new Date(Date.now() - 200 * 24 * 60 * 60 * 1000).toISOString(); // 200 days ago

const VERIFIED_CONTACT: ContactRead = {
  id: "contact-1",
  entity_id: "ent-abc",
  name: "Dr. Lisa Hong",
  department: "Curriculum",
  title: "Director of Curriculum",
  status: "active",
  canonical_email: "lhong@nsd.org",
  attributes: {},
  source: "nsd.org/staff",
  source_url: "https://nsd.org/staff",
  source_recipe_id: null,
  confidence: 0.95,
  observed_at: NOW_ISO,
  verified: true,
  last_verified_at: NOW_ISO,
  created_at: NOW_ISO,
  updated_at: NOW_ISO,
};

const STALE_CONTACT_UNVERIFIED: ContactRead = {
  id: "contact-2",
  entity_id: "ent-abc",
  name: "James Park",
  department: null,
  title: "CIO",
  status: "active",
  canonical_email: "jpark@nsd.org",
  attributes: {},
  source: "nsd.org/leadership",
  source_url: null,
  source_recipe_id: null,
  confidence: 0.7,
  observed_at: NOW_ISO,
  verified: false,
  last_verified_at: null,
  created_at: NOW_ISO,
  updated_at: NOW_ISO,
};

const STALE_CONTACT_OLD: ContactRead = {
  id: "contact-3",
  entity_id: "ent-abc",
  name: "Sara Ortiz",
  department: "Administration",
  title: "Asst. Supt. Instruction",
  status: "active",
  canonical_email: "sortiz@nsd.org",
  attributes: {},
  source: "nsd.org/staff",
  source_url: "https://nsd.org/staff",
  source_recipe_id: null,
  confidence: 0.8,
  observed_at: OLD_ISO,
  // verified=true but last_verified_at is 200 days ago → Stale
  verified: true,
  last_verified_at: OLD_ISO,
  created_at: OLD_ISO,
  updated_at: OLD_ISO,
};

// ---- Helpers ----

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function pathOf(input: RequestInfo | URL): string {
  return new URL(String(input), "http://localhost").pathname;
}

// ---- Tests ----

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ContactList", () => {
  describe("renders contacts", () => {
    beforeEach(() => {
      vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path === "/api/v1/contacts") {
          const page: ContactPage = {
            items: [VERIFIED_CONTACT, STALE_CONTACT_UNVERIFIED],
            next_cursor: null,
          };
          return jsonResponse(page);
        }
        return jsonResponse({ items: [], next_cursor: null });
      });
    });

    it("renders contact names", async () => {
      render(<ContactList entityId="ent-abc" />, { wrapper });
      expect(await screen.findByText("Dr. Lisa Hong")).toBeTruthy();
      expect(screen.getByText("James Park")).toBeTruthy();
    });

    it("renders title/role for each contact", async () => {
      render(<ContactList entityId="ent-abc" />, { wrapper });
      await screen.findByText("Dr. Lisa Hong");
      expect(screen.getByText(/Director of Curriculum/)).toBeTruthy();
      expect(screen.getByText(/CIO/)).toBeTruthy();
    });

    it("renders email as a mailto link", async () => {
      render(<ContactList entityId="ent-abc" />, { wrapper });
      await screen.findByText("Dr. Lisa Hong");
      const emailLink = screen.getByRole("link", { name: "lhong@nsd.org" });
      expect(emailLink.getAttribute("href")).toBe("mailto:lhong@nsd.org");
    });

    it("renders Verified badge for a recently-verified contact", async () => {
      render(<ContactList entityId="ent-abc" />, { wrapper });
      await screen.findByText("Dr. Lisa Hong");
      const badges = screen.getAllByTestId("verification-badge");
      const verifiedBadge = badges.find((b) => b.textContent === "Verified");
      expect(verifiedBadge).toBeTruthy();
    });

    it("renders Stale badge for an unverified contact", async () => {
      render(<ContactList entityId="ent-abc" />, { wrapper });
      await screen.findByText("James Park");
      const badges = screen.getAllByTestId("verification-badge");
      const staleBadge = badges.find((b) => b.textContent === "Stale");
      expect(staleBadge).toBeTruthy();
    });

    it("renders source provenance link when source_url is set", async () => {
      render(<ContactList entityId="ent-abc" />, { wrapper });
      await screen.findByText("Dr. Lisa Hong");
      const sourceLink = screen.getByRole("link", { name: "nsd.org/staff" });
      expect(sourceLink.getAttribute("href")).toBe("https://nsd.org/staff");
    });

    it("fetches contacts using entity_id query param", async () => {
      const fetchSpy = vi
        .spyOn(globalThis, "fetch")
        .mockImplementation(async () =>
          jsonResponse({ items: [], next_cursor: null }),
        );

      render(<ContactList entityId="ent-abc" />, { wrapper });

      await waitFor(() => expect(fetchSpy).toHaveBeenCalled());

      const calledUrl = String(fetchSpy.mock.calls[0][0]);
      expect(calledUrl).toContain("entity_id=ent-abc");
    });
  });

  describe("Stale badge for verified-but-old contact", () => {
    it("shows Stale when last_verified_at is older than 180 days", async () => {
      vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path === "/api/v1/contacts") {
          return jsonResponse({
            items: [STALE_CONTACT_OLD],
            next_cursor: null,
          });
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

      render(<ContactList entityId="ent-abc" />, { wrapper });
      await screen.findByText("Sara Ortiz");
      const badge = screen.getByTestId("verification-badge");
      expect(badge.textContent).toBe("Stale");
    });
  });

  describe("empty state", () => {
    it("shows empty message when the API returns no contacts", async () => {
      vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
        jsonResponse({ items: [], next_cursor: null }),
      );

      render(<ContactList entityId="ent-abc" />, { wrapper });
      expect(
        await screen.findByText(/No contacts on record/i),
      ).toBeTruthy();
    });
  });

  describe("error state", () => {
    it("shows an error alert when the API returns a 500", async () => {
      vi.spyOn(globalThis, "fetch").mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Internal Server Error",
            status: 500,
            detail: "Unexpected error.",
          }),
          {
            status: 500,
            headers: { "Content-Type": "application/problem+json" },
          },
        ),
      );

      render(<ContactList entityId="ent-abc" />, { wrapper });

      expect(await screen.findByRole("alert")).toBeTruthy();
    });
  });

  describe("load more", () => {
    it("shows 'Load more contacts' button when next_cursor is set", async () => {
      vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path === "/api/v1/contacts") {
          return jsonResponse({
            items: [VERIFIED_CONTACT],
            next_cursor: "cursor-p2",
          });
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

      render(<ContactList entityId="ent-abc" />, { wrapper });
      expect(
        await screen.findByRole("button", { name: /load more contacts/i }),
      ).toBeTruthy();
    });

    it("fetches next page when 'Load more contacts' is clicked", async () => {
      let callCount = 0;
      vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const path = pathOf(input);
        if (path === "/api/v1/contacts") {
          callCount += 1;
          if (callCount === 1) {
            return jsonResponse({
              items: [VERIFIED_CONTACT],
              next_cursor: "cursor-p2",
            });
          }
          return jsonResponse({ items: [STALE_CONTACT_UNVERIFIED], next_cursor: null });
        }
        return jsonResponse({ items: [], next_cursor: null });
      });

      const user = userEvent.setup();
      render(<ContactList entityId="ent-abc" />, { wrapper });

      const btn = await screen.findByRole("button", { name: /load more contacts/i });
      await user.click(btn);
      await waitFor(() => expect(callCount).toBe(2));
    });
  });
});
