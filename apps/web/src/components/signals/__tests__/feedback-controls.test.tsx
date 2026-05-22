// @vitest-environment jsdom
// F5 — FeedbackControls + useSignalFeedback:
// - renders a button per verdict, marks the current one active (aria-pressed)
// - clicking a verdict fires the POST with (signalId, kind)
// - clicking the active verdict retracts it (DELETE; kind null)
// - the mutation optimistically patches the detail cache's `feedback` and rolls
//   back on error.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";

afterEach(() => {
  cleanup();
  // Reset call history between tests so per-test "not called" assertions are not
  // tripped by a sibling test's call (the spies are module-level).
  vi.clearAllMocks();
});

// ---- Mock the transport so no real fetch happens ---------------------------

const submitFeedbackMock = vi.fn();
const retractFeedbackMock = vi.fn();
vi.mock("@/lib/signals-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/signals-api")>();
  return {
    ...actual,
    submitSignalFeedback: (...args: unknown[]) => submitFeedbackMock(...args),
    retractSignalFeedback: (...args: unknown[]) => retractFeedbackMock(...args),
  };
});

// ---- Mock the auth/workspace stores so the hook has a token + workspace ----

vi.mock("@/store/session", () => ({
  useSessionStore: (selector: (s: { accessToken: string | null }) => unknown) =>
    selector({ accessToken: "tok-123" }),
}));
vi.mock("@/store/ui", () => ({
  useUiStore: (selector: (s: { activeWorkspaceId: string | null }) => unknown) =>
    selector({ activeWorkspaceId: "ws-1" }),
}));

import { FeedbackControls } from "@/components/signals/feedback-controls";
import { useSignalFeedback, feedKeys } from "@/hooks/use-signals";
import type { SignalDetailRead } from "@/lib/signals-api";

// ---- Wrappers --------------------------------------------------------------

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  });
}

function wrapperFor(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

const NOW_ISO = "2026-05-22T12:00:00Z";

function detail(
  signalId: string,
  feedback: SignalDetailRead["feedback"],
): SignalDetailRead {
  return {
    signal: {
      id: signalId,
      entity_id: "ent-1",
      entity_name_raw: "Northshore SD",
      signal_type: "rfp_posted",
      recipe_id: "r",
      raw_document_ids: [],
      content_hash: "abc",
      occurred_at: NOW_ISO,
      observed_at: NOW_ISO,
      summary: "s",
      title: "RFP",
      details: {},
      confidence: 0.9,
      status: "new",
      is_degraded: false,
      review_required: false,
      created_at: NOW_ISO,
    },
    entity_id: "ent-1",
    entity_name: "Northshore SD",
    score: 80,
    status: "new",
    score_breakdown: {},
    matched_keywords: [],
    extracted_fields: {},
    source_documents: [],
    suggested_contacts: [],
    related_signals: [],
    feedback,
  };
}

// ---- Component tests -------------------------------------------------------

describe("FeedbackControls — renders verdicts + reflects current selection", () => {
  it("renders all three verdict buttons", () => {
    const client = makeClient();
    render(<FeedbackControls signalId="sig-1" current={null} />, {
      wrapper: wrapperFor(client),
    });
    expect(screen.getByTestId("feedback-action-relevant")).toBeTruthy();
    expect(screen.getByTestId("feedback-action-not_relevant")).toBeTruthy();
    expect(screen.getByTestId("feedback-action-wrong_extraction")).toBeTruthy();
  });

  it("marks the current verdict active via aria-pressed", () => {
    const client = makeClient();
    render(<FeedbackControls signalId="sig-1" current="relevant" />, {
      wrapper: wrapperFor(client),
    });
    expect(
      screen.getByTestId("feedback-action-relevant").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(
      screen
        .getByTestId("feedback-action-not_relevant")
        .getAttribute("aria-pressed"),
    ).toBe("false");
  });

  it("clicking 'Not relevant' fires the POST with (signalId, 'not_relevant')", async () => {
    submitFeedbackMock.mockResolvedValueOnce({
      signal_id: "sig-1",
      kind: "not_relevant",
    });
    const client = makeClient();
    render(<FeedbackControls signalId="sig-1" current={null} />, {
      wrapper: wrapperFor(client),
    });
    fireEvent.click(screen.getByTestId("feedback-action-not_relevant"));
    await waitFor(() => {
      expect(submitFeedbackMock).toHaveBeenCalledWith(
        "tok-123",
        "ws-1",
        "sig-1",
        "not_relevant",
      );
    });
  });

  it("clicking the active verdict retracts it (DELETE)", async () => {
    retractFeedbackMock.mockResolvedValueOnce({ signal_id: "sig-1", kind: "" });
    const client = makeClient();
    render(<FeedbackControls signalId="sig-1" current="relevant" />, {
      wrapper: wrapperFor(client),
    });
    fireEvent.click(screen.getByTestId("feedback-action-relevant"));
    await waitFor(() => {
      expect(retractFeedbackMock).toHaveBeenCalledWith("tok-123", "ws-1", "sig-1");
    });
    expect(submitFeedbackMock).not.toHaveBeenCalled();
  });
});

// ---- Hook optimistic-update tests ------------------------------------------

describe("useSignalFeedback — optimistic update", () => {
  it("optimistically patches the detail cache's feedback before the request resolves", async () => {
    const client = makeClient();
    const detailKey = feedKeys.detail("ws-1", "sig-1");
    client.setQueryData<SignalDetailRead>(detailKey, detail("sig-1", null));

    // Never-resolving request so we observe the optimistic state mid-flight.
    let resolve!: (v: unknown) => void;
    submitFeedbackMock.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );

    const { result } = renderHook(() => useSignalFeedback(), {
      wrapper: wrapperFor(client),
    });

    act(() => {
      result.current.mutate({ signalId: "sig-1", kind: "relevant" });
    });

    await waitFor(() => {
      const d = client.getQueryData<SignalDetailRead>(detailKey);
      expect(d?.feedback).toBe("relevant");
    });

    act(() => {
      resolve({ signal_id: "sig-1", kind: "relevant" });
    });
  });

  it("rolls the detail cache back to the snapshot on error", async () => {
    const client = makeClient();
    const detailKey = feedKeys.detail("ws-1", "sig-1");
    client.setQueryData<SignalDetailRead>(detailKey, detail("sig-1", "relevant"));

    submitFeedbackMock.mockRejectedValueOnce(new Error("boom"));

    const { result } = renderHook(() => useSignalFeedback(), {
      wrapper: wrapperFor(client),
    });

    await act(async () => {
      await result.current
        .mutateAsync({ signalId: "sig-1", kind: "not_relevant" })
        .catch(() => undefined);
    });

    await waitFor(() => {
      const d = client.getQueryData<SignalDetailRead>(detailKey);
      expect(d?.feedback).toBe("relevant");
    });
  });
});
