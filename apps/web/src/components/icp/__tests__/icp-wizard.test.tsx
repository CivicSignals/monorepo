// @vitest-environment jsdom
// F2 — Integration tests for the ICP onboarding wizard.
//
// Tests:
// 1. Step navigation: can advance through all 6 steps.
// 2. Per-step validation: signal-types step blocks "Continue" without a selection.
// 3. Review step: assembles and submits the correct ICP payload (mocked API).
// 4. Edit mode: wizard pre-fills from a provided ICP (via store pre-population).

import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

// ---- Module mocks ----
// next/navigation mock (useRouter)
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// ---- Component under test (imported after mocks) ----
import { IcpWizard } from "../icp-wizard";

// ---- Wizard store reset between tests ----
import { useIcpWizardStore } from "@/store/icp-wizard";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

// ---- Test wrapper ----

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  // Reset wizard store to defaults before each test.
  useIcpWizardStore.setState({
    currentStep: "geography",
    draft: {
      name: "My ICP",
      countries: ["US"],
      states: [],
      entity_kinds: [],
      signal_types: [],
      signal_weights: {},
      min_size: null,
      max_size: null,
      keywords_required: [],
      keywords_excluded: [],
      deal_band_min_cents: null,
      deal_band_max_cents: null,
      threshold: 50,
    },
    editingId: null,
  });
  // Set a fake token + workspace so mutations don't short-circuit.
  useSessionStore.setState({
    accessToken: "test-token",
    user: {
      id: "user-1",
      email: "maya@example.com",
      name: "Maya",
      email_verified: true,
      created_at: "2026-05-22T00:00:00Z",
    },
    setSession: () => {},
    setUser: () => {},
    clear: () => {},
  });
  useUiStore.setState({
    activeWorkspaceId: "ws-test-1",
    setActiveWorkspaceId: () => {},
  });
  localStorage.clear();
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ---- Helpers to navigate each step ----

/** Advance the geography step with a valid name. */
async function submitGeography(user: ReturnType<typeof userEvent.setup>) {
  const nameInput = screen.getByLabelText(/icp name/i);
  await user.clear(nameInput);
  await user.type(nameInput, "Test ICP");
  await user.click(screen.getByRole("button", { name: /continue/i }));
}

/** Advance the segments step (empty = all entity kinds → just click Continue). */
async function submitSegments(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /continue/i }));
}

/** Advance the size step with no values → just Continue. */
async function submitSize(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /continue/i }));
}

/** Select "RFP Posted" then advance signals step. */
async function submitSignals(user: ReturnType<typeof userEvent.setup>) {
  const rfpCheckbox = screen.getByRole("checkbox", { name: /rfp posted/i });
  await user.click(rfpCheckbox);
  await user.click(screen.getByRole("button", { name: /continue/i }));
}

/** Advance the keywords step with defaults. */
async function submitKeywords(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /continue/i }));
}

// ---- Tests ----

describe("IcpWizard — step navigation", () => {
  it("starts on the geography step and shows the step indicator", () => {
    render(<IcpWizard />, { wrapper });
    expect(screen.getByLabelText(/wizard progress/i)).toBeTruthy();
    expect(screen.getByLabelText(/icp name/i)).toBeTruthy();
  });

  it("advances from geography → segments when Continue is clicked with a valid name", async () => {
    const user = userEvent.setup();
    render(<IcpWizard />, { wrapper });

    await submitGeography(user);

    await waitFor(() =>
      expect(screen.getByText(/who are you selling to/i)).toBeTruthy(),
    );
  });

  it("navigates back from segments to geography", async () => {
    const user = userEvent.setup();
    render(<IcpWizard />, { wrapper });

    await submitGeography(user);
    await waitFor(() => screen.getByText(/who are you selling to/i));

    await user.click(screen.getByRole("button", { name: /back/i }));

    await waitFor(() =>
      expect(screen.getByLabelText(/icp name/i)).toBeTruthy(),
    );
  });

  it("reaches the review step after completing all 5 data steps", async () => {
    const user = userEvent.setup();
    render(<IcpWizard />, { wrapper });

    await submitGeography(user);
    await waitFor(() => screen.getByText(/who are you selling to/i));

    await submitSegments(user);
    await waitFor(() => screen.getByText(/target size band/i));

    await submitSize(user);
    await waitFor(() => screen.getByText(/which signals matter/i));

    await submitSignals(user);
    await waitFor(() => screen.getByText(/keywords & scoring threshold/i));

    await submitKeywords(user);
    await waitFor(() =>
      expect(screen.getByText(/review your icp/i)).toBeTruthy(),
    );
  });
});

describe("IcpWizard — per-step validation", () => {
  it("blocks Continue on the geography step when name is empty", async () => {
    const user = userEvent.setup();
    render(<IcpWizard />, { wrapper });

    // Clear the pre-filled name and try to continue.
    const nameInput = screen.getByLabelText(/icp name/i);
    await user.clear(nameInput);
    await user.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/give your icp a name/i),
      ).toBeTruthy(),
    );
    // Still on geography step.
    expect(screen.getByLabelText(/icp name/i)).toBeTruthy();
  });

  it("blocks Continue on the signals step when no signal type is selected", async () => {
    const user = userEvent.setup();
    render(<IcpWizard />, { wrapper });

    // Navigate to signals step.
    await submitGeography(user);
    await waitFor(() => screen.getByText(/who are you selling to/i));
    await submitSegments(user);
    await waitFor(() => screen.getByText(/target size band/i));
    await submitSize(user);
    await waitFor(() => screen.getByText(/which signals matter/i));

    // Don't select any signal type — click Continue.
    await user.click(screen.getByRole("button", { name: /continue/i }));

    await waitFor(() =>
      expect(
        screen.getByText(/select at least one signal type/i),
      ).toBeTruthy(),
    );
    // Still on signals step.
    expect(screen.getByText(/which signals matter/i)).toBeTruthy();
  });
});

describe("IcpWizard — review step submits correct payload", () => {
  it("calls POST /icp and POST /icp/{id}/activate with the assembled payload", async () => {
    const CREATED_ICP_ID = "test-icp-uuid-1234";
    const fetchSpy: MockInstance = vi.spyOn(globalThis, "fetch").mockImplementation(
      async (input: RequestInfo | URL) => {
        const url = String(input);
        // POST /icp → created ICP
        if (url.endsWith("/icp") && !url.includes("/activate")) {
          return new Response(
            JSON.stringify({
              id: CREATED_ICP_ID,
              workspace_id: "ws-1",
              name: "Test ICP",
              countries: ["US"],
              states: [],
              entity_kinds: [],
              signal_types: ["rfp_posted"],
              signal_weights: { rfp_posted: 1.0 },
              min_size: null,
              max_size: null,
              keywords_required: [],
              keywords_excluded: [],
              deal_band_min_cents: null,
              deal_band_max_cents: null,
              threshold: 50,
              is_active: false,
              created_at: "2026-05-22T00:00:00Z",
              updated_at: "2026-05-22T00:00:00Z",
            }),
            { status: 201, headers: { "Content-Type": "application/json" } },
          );
        }
        // POST /icp/{id}/activate
        if (url.includes("/activate")) {
          return new Response(
            JSON.stringify({
              id: CREATED_ICP_ID,
              workspace_id: "ws-1",
              name: "Test ICP",
              countries: ["US"],
              states: [],
              entity_kinds: [],
              signal_types: ["rfp_posted"],
              signal_weights: { rfp_posted: 1.0 },
              min_size: null,
              max_size: null,
              keywords_required: [],
              keywords_excluded: [],
              deal_band_min_cents: null,
              deal_band_max_cents: null,
              threshold: 50,
              is_active: true,
              created_at: "2026-05-22T00:00:00Z",
              updated_at: "2026-05-22T00:00:00Z",
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          );
        }
        return new Response(null, { status: 404 });
      },
    );

    const user = userEvent.setup();
    render(<IcpWizard />, { wrapper });

    // Navigate through all steps.
    await submitGeography(user);
    await waitFor(() => screen.getByText(/who are you selling to/i));
    await submitSegments(user);
    await waitFor(() => screen.getByText(/target size band/i));
    await submitSize(user);
    await waitFor(() => screen.getByText(/which signals matter/i));
    await submitSignals(user);
    await waitFor(() => screen.getByText(/keywords & scoring threshold/i));
    await submitKeywords(user);
    await waitFor(() => screen.getByText(/review your icp/i));

    // Click "Create & activate ICP".
    await user.click(
      screen.getByRole("button", { name: /create & activate icp/i }),
    );

    // Wait for both API calls to complete.
    await waitFor(() => {
      const calls = fetchSpy.mock.calls.map(([url]) => String(url));
      expect(calls.some((u) => u.endsWith("/icp"))).toBe(true);
      expect(calls.some((u) => u.includes("/activate"))).toBe(true);
    });

    // Verify POST /icp was called with rfp_posted signal type.
    const createCall = fetchSpy.mock.calls.find(([url]) =>
      String(url).endsWith("/icp"),
    );
    expect(createCall).toBeTruthy();
    const body = JSON.parse(createCall![1]?.body as string) as Record<string, unknown>;
    expect(body.signal_types).toContain("rfp_posted");
    expect(body.name).toBe("Test ICP");
  });
});

describe("IcpWizard — edit mode prefills from existing ICP", () => {
  it("pre-populates the geography step when the store is loaded from an ICP", async () => {
    // Simulate the wizard store already loaded from an existing ICP
    // (this is what useLoadFromExisting does when the TanStack Query resolves).
    // In a real app the query would need a token; here we test the store
    // hydration path directly, which is the behaviour being asserted.
    useIcpWizardStore.setState({
      currentStep: "geography",
      editingId: "existing-icp-uuid",
      draft: {
        name: "Existing K-12 ICP",
        countries: ["US"],
        states: ["CA", "WA"],
        entity_kinds: ["k12_district"],
        signal_types: ["rfp_posted", "budget_drafted"],
        signal_weights: { rfp_posted: 0.9, budget_drafted: 0.7 },
        min_size: 5000,
        max_size: null,
        keywords_required: ["analytics"],
        keywords_excluded: [],
        deal_band_min_cents: null,
        deal_band_max_cents: null,
        threshold: 65,
      },
    });

    render(<IcpWizard />, { wrapper });

    // The geography step should show the pre-filled name.
    const nameInput = await screen.findByLabelText<HTMLInputElement>(/icp name/i);
    expect(nameInput.value).toBe("Existing K-12 ICP");
  });

  it("shows Save changes button on the review step in edit mode", async () => {
    // Set up edit mode draft in store.
    useIcpWizardStore.setState({
      currentStep: "review",
      editingId: "existing-icp-uuid",
      draft: {
        name: "Existing K-12 ICP",
        countries: ["US"],
        states: [],
        entity_kinds: ["k12_district"],
        signal_types: ["rfp_posted"],
        signal_weights: { rfp_posted: 1.0 },
        min_size: null,
        max_size: null,
        keywords_required: [],
        keywords_excluded: [],
        deal_band_min_cents: null,
        deal_band_max_cents: null,
        threshold: 65,
      },
    });

    render(<IcpWizard />, { wrapper });

    await waitFor(() =>
      expect(screen.getByText(/review your icp/i)).toBeTruthy(),
    );
    // Edit mode shows "Save changes" not "Create & activate".
    expect(screen.getByRole("button", { name: /save changes/i })).toBeTruthy();
  });
});
