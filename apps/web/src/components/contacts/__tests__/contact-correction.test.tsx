// @vitest-environment jsdom
// C6 — Contact correction: "Report incorrect" affordance on ContactCard.
//
// Tests:
// - Report button renders when authContext is provided.
// - Report button is absent without authContext (read-only mode).
// - Clicking "Report incorrect" shows the correction form.
// - Submitting the form calls the POST /contacts/{id}/report-invalid endpoint.
// - Success response updates the badge (Bounced/Invalid) via query invalidation.
// - Error from the API shows an error alert inside the form.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { ContactCard } from "@/components/contacts/contact-card";
import type { ContactRead, ContactCorrectionResponse } from "@/lib/contacts-api";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

// ---- Fixtures ----

const NOW_ISO = new Date().toISOString();

const ACTIVE_CONTACT: ContactRead = {
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
  reported_invalid_at: null,
  bounce_count: 0,
  created_at: NOW_ISO,
  updated_at: NOW_ISO,
};

const BOUNCED_CONTACT: ContactRead = {
  ...ACTIVE_CONTACT,
  id: "contact-2",
  status: "bounced",
  verified: false,
  last_verified_at: null,
  reported_invalid_at: NOW_ISO,
  bounce_count: 1,
};

const INVALID_CONTACT: ContactRead = {
  ...ACTIVE_CONTACT,
  id: "contact-3",
  status: "invalid",
  verified: false,
  last_verified_at: null,
  reported_invalid_at: NOW_ISO,
  bounce_count: 1,
};

const AUTH_CONTEXT = {
  accessToken: "test-token",
  workspaceId: "ws-abc",
  entityId: "ent-abc",
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- Tests ----

describe("ContactCard — C6 correction affordance", () => {
  it("renders 'Report incorrect' button when authContext is provided", () => {
    render(<ContactCard contact={ACTIVE_CONTACT} authContext={AUTH_CONTEXT} />, {
      wrapper,
    });
    expect(screen.getByTestId("report-incorrect-button")).toBeTruthy();
  });

  it("does NOT render report button without authContext (read-only mode)", () => {
    render(<ContactCard contact={ACTIVE_CONTACT} />, { wrapper });
    expect(screen.queryByTestId("report-incorrect-button")).toBeNull();
  });

  it("shows correction form when 'Report incorrect' is clicked", async () => {
    const user = userEvent.setup();
    render(<ContactCard contact={ACTIVE_CONTACT} authContext={AUTH_CONTEXT} />, {
      wrapper,
    });

    await user.click(screen.getByTestId("report-incorrect-button"));

    expect(screen.getByTestId("correction-kind-select")).toBeTruthy();
    expect(screen.getByTestId("correction-reason-input")).toBeTruthy();
    expect(screen.getByTestId("correction-submit")).toBeTruthy();
    expect(screen.getByTestId("correction-cancel")).toBeTruthy();
  });

  it("hides the form when Cancel is clicked", async () => {
    const user = userEvent.setup();
    render(<ContactCard contact={ACTIVE_CONTACT} authContext={AUTH_CONTEXT} />, {
      wrapper,
    });

    await user.click(screen.getByTestId("report-incorrect-button"));
    // Verify form appeared.
    expect(screen.getByTestId("correction-cancel")).toBeTruthy();

    await user.click(screen.getByTestId("correction-cancel"));
    expect(screen.queryByTestId("correction-cancel")).toBeNull();
    expect(screen.getByTestId("report-incorrect-button")).toBeTruthy();
  });

  it("calls POST /contacts/{id}/report-invalid when form is submitted", async () => {
    const user = userEvent.setup();
    const successResponse: ContactCorrectionResponse = {
      contact: { ...ACTIVE_CONTACT, status: "bounced", verified: false, bounce_count: 1 },
      correction: {
        id: "corr-1",
        contact_id: ACTIVE_CONTACT.id,
        workspace_id: AUTH_CONTEXT.workspaceId,
        reporter_id: "user-1",
        kind: "bounced",
        reason: "Email bounced",
        correction: null,
        created_at: NOW_ISO,
      },
    };

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(successResponse, 201),
    );

    render(<ContactCard contact={ACTIVE_CONTACT} authContext={AUTH_CONTEXT} />, {
      wrapper,
    });

    await user.click(screen.getByTestId("report-incorrect-button"));

    // Select "bounced" kind.
    const kindSelect = screen.getByTestId("correction-kind-select");
    await user.selectOptions(kindSelect, "bounced");

    // Fill in reason.
    const reasonInput = screen.getByTestId("correction-reason-input");
    await user.type(reasonInput, "Email bounced");

    await user.click(screen.getByTestId("correction-submit"));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledOnce();
      const calledUrl = String(fetchSpy.mock.calls[0][0]);
      expect(calledUrl).toContain(`/contacts/${ACTIVE_CONTACT.id}/report-invalid`);
    });

    // Verify request included auth headers.
    const calledInit = fetchSpy.mock.calls[0][1] as RequestInit;
    const headers = new Headers(calledInit?.headers);
    expect(headers.get("Authorization")).toBe(`Bearer ${AUTH_CONTEXT.accessToken}`);
    expect(headers.get("X-Workspace-Id")).toBe(AUTH_CONTEXT.workspaceId);
  });

  it("shows error alert when the API returns an error", async () => {
    const user = userEvent.setup();

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: "Contact not found.",
        }),
        { status: 404, headers: { "Content-Type": "application/problem+json" } },
      ),
    );

    render(<ContactCard contact={ACTIVE_CONTACT} authContext={AUTH_CONTEXT} />, {
      wrapper,
    });

    await user.click(screen.getByTestId("report-incorrect-button"));
    await user.click(screen.getByTestId("correction-submit"));

    expect(await screen.findByRole("alert")).toBeTruthy();
  });
});

describe("ContactCard — C6 bounced/invalid badge", () => {
  it("renders 'Bounced' badge when contact.status is 'bounced'", () => {
    render(<ContactCard contact={BOUNCED_CONTACT} />, { wrapper });
    const badge = screen.getByTestId("verification-badge");
    expect(badge.textContent).toBe("Bounced");
    expect(badge.getAttribute("aria-label")).toBe("Bounced");
  });

  it("renders 'Invalid' badge when contact.status is 'invalid'", () => {
    render(<ContactCard contact={INVALID_CONTACT} />, { wrapper });
    const badge = screen.getByTestId("verification-badge");
    expect(badge.textContent).toBe("Invalid");
    expect(badge.getAttribute("aria-label")).toBe("Invalid");
  });
});
