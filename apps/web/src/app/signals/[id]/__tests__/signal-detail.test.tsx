// @vitest-environment jsdom
// G2 — SignalDetail: renders sections from mocked data (header/score, fields,
// source docs, suggested contacts, related signals, inspect panel), plus
// loading, error, and not-found states.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

afterEach(cleanup);

// ---- Mock next/link as a plain anchor --------------------------------------

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// ---- Mock the signals hook so we control data without a real API -----------

vi.mock("@/hooks/use-signals", () => ({
  useSignalDetail: vi.fn(),
}));

import { useSignalDetail } from "@/hooks/use-signals";
import { SignalDetail } from "@/app/signals/[id]/signal-detail";
import type { SignalDetailRead } from "@/lib/signals-api";
import { ProblemError } from "@/lib/auth-api";

// ---- Test wrapper ----------------------------------------------------------

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

// ---- Fixtures --------------------------------------------------------------

const NOW_ISO = "2026-05-22T12:00:00Z";

function makeDetail(over: Partial<SignalDetailRead> = {}): SignalDetailRead {
  return {
    signal: {
      id: "sig-001",
      entity_id: "ent-001",
      entity_name_raw: "Northshore School District",
      signal_type: "rfp_posted",
      recipe_id: "k12_rfp_v3",
      raw_document_ids: ["doc-1"],
      content_hash: "abc",
      occurred_at: NOW_ISO,
      observed_at: NOW_ISO,
      summary: "An RFP for K-8 math curriculum.",
      title: "RFP: K-8 Math Curriculum",
      details: { rfp_number: "R-2026-01", amount_cents: 500000 },
      confidence: 0.9,
      status: "new",
      is_degraded: false,
      review_required: false,
      created_at: NOW_ISO,
    },
    entity_id: "ent-001",
    entity_name: "Northshore School District",
    score: 88,
    status: "new",
    score_breakdown: { keywords: 12, bullets: ["matched WA"] },
    matched_keywords: ["curriculum"],
    extracted_fields: { rfp_number: "R-2026-01", amount_cents: 500000 },
    source_documents: [
      {
        raw_document_id: "doc-1",
        recipe_id: "k12_rfp_v3",
        source_url: "https://city.gov/rfp/123",
        fetched_at: NOW_ISO,
        content_type: "text/html",
        missing: false,
      },
    ],
    suggested_contacts: [
      {
        contact_id: "c-1",
        name: "Jane Doe",
        title: "Procurement Director",
        department: "Purchasing",
        canonical_email: "jane.doe@city.gov",
        status: "active",
        verified: true,
      },
    ],
    related_signals: [
      {
        signal: {
          ...makeSignalCore(),
          id: "sig-002",
          title: "Budget approved for curriculum",
          signal_type: "budget_approved",
        },
      },
    ],
    ...over,
  };
}

function makeSignalCore() {
  return {
    id: "sig-x",
    entity_id: "ent-001",
    entity_name_raw: "Northshore School District",
    signal_type: "rfp_posted",
    recipe_id: "r",
    raw_document_ids: [],
    content_hash: "h",
    occurred_at: NOW_ISO,
    observed_at: NOW_ISO,
    summary: "s",
    title: "t",
    details: {},
    confidence: null,
    status: "new",
    is_degraded: false,
    review_required: false,
    created_at: NOW_ISO,
  };
}

type MockReturn = ReturnType<typeof useSignalDetail>;

function stub(over: {
  data?: SignalDetailRead;
  isLoading?: boolean;
  error?: Error | null;
}): MockReturn {
  return {
    data: undefined,
    isLoading: false,
    error: null,
    ...over,
  } as MockReturn;
}

const mockUseSignalDetail = vi.mocked(useSignalDetail);

// ---- Tests -----------------------------------------------------------------

describe("SignalDetail — loaded view", () => {
  it("renders the title, type label, and workspace score band", () => {
    mockUseSignalDetail.mockReturnValue(stub({ data: makeDetail() }) as MockReturn);
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.getByTestId("signal-title").textContent).toContain(
      "RFP: K-8 Math Curriculum",
    );
    expect(screen.getByText("RFP Posted")).toBeTruthy();
    expect(screen.getByTestId("signal-score").textContent).toContain("High");
    expect(screen.getByTestId("signal-score").textContent).toContain("88");
  });

  it("renders extracted fields excluding title/summary/type", () => {
    mockUseSignalDetail.mockReturnValue(stub({ data: makeDetail() }) as MockReturn);
    render(<SignalDetail id="sig-001" />, { wrapper });
    const fields = screen.getByTestId("signal-fields");
    expect(fields.textContent).toContain("Rfp Number");
    expect(fields.textContent).toContain("R-2026-01");
  });

  it("renders source documents with a link", () => {
    mockUseSignalDetail.mockReturnValue(stub({ data: makeDetail() }) as MockReturn);
    render(<SignalDetail id="sig-001" />, { wrapper });
    const docs = screen.getAllByTestId("source-doc");
    expect(docs.length).toBe(1);
    expect(screen.getByText("https://city.gov/rfp/123")).toBeTruthy();
  });

  it("renders a tombstone for a missing source document", () => {
    mockUseSignalDetail.mockReturnValue(
      stub({
        data: makeDetail({
          source_documents: [
            {
              raw_document_id: "gone",
              recipe_id: null,
              source_url: null,
              fetched_at: null,
              content_type: null,
              missing: true,
            },
          ],
        }),
      }) as MockReturn,
    );
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.getByTestId("source-doc-missing")).toBeTruthy();
  });

  it("renders suggested contacts", () => {
    mockUseSignalDetail.mockReturnValue(stub({ data: makeDetail() }) as MockReturn);
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.getByTestId("suggested-contact").textContent).toContain("Jane Doe");
    expect(screen.getByText("jane.doe@city.gov")).toBeTruthy();
  });

  it("renders related signals linking to their detail pages", () => {
    mockUseSignalDetail.mockReturnValue(stub({ data: makeDetail() }) as MockReturn);
    render(<SignalDetail id="sig-001" />, { wrapper });
    const related = screen.getAllByTestId("related-signal");
    expect(related.length).toBe(1);
    const link = related[0].querySelector("a");
    expect(link?.getAttribute("href")).toBe("/signals/sig-002");
  });

  it("renders the inspect panel with score breakdown JSON", () => {
    mockUseSignalDetail.mockReturnValue(stub({ data: makeDetail() }) as MockReturn);
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.getByTestId("signal-inspect")).toBeTruthy();
    expect(screen.getByTestId("inspect-breakdown").textContent).toContain("keywords");
  });

  it("hides the score band when the signal has no workspace score", () => {
    mockUseSignalDetail.mockReturnValue(
      stub({ data: makeDetail({ score: null, status: null, score_breakdown: null }) }) as MockReturn,
    );
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.queryByTestId("signal-score")).toBeNull();
    // Still renders the title (signal is global, viewable without a score).
    expect(screen.getByTestId("signal-title")).toBeTruthy();
  });

  it("shows empty-state copy when there are no contacts or related signals", () => {
    mockUseSignalDetail.mockReturnValue(
      stub({ data: makeDetail({ suggested_contacts: [], related_signals: [] }) }) as MockReturn,
    );
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.getByTestId("signal-contacts").textContent).toContain("No contacts");
    expect(screen.getByTestId("signal-related").textContent).toContain(
      "No other signals",
    );
  });
});

describe("SignalDetail — loading / error / not-found", () => {
  it("shows the skeleton while loading", () => {
    mockUseSignalDetail.mockReturnValue(stub({ isLoading: true }) as MockReturn);
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.getByTestId("signal-loading")).toBeTruthy();
  });

  it("shows a not-found state on a 404 problem", () => {
    mockUseSignalDetail.mockReturnValue(
      stub({
        error: new ProblemError({
          type: "about:blank",
          title: "Signal not found",
          status: 404,
          detail: "No signal with id sig-001.",
        }),
      }) as MockReturn,
    );
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.getByTestId("signal-not-found")).toBeTruthy();
  });

  it("shows an error alert on a non-404 error", () => {
    mockUseSignalDetail.mockReturnValue(
      stub({ error: new Error("Service unavailable") }) as MockReturn,
    );
    render(<SignalDetail id="sig-001" />, { wrapper });
    expect(screen.getByTestId("signal-error").textContent).toContain(
      "Service unavailable",
    );
  });
});
