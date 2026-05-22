// @vitest-environment jsdom
// G4 — StatusControls + useChangeSignalStatus:
// - renders the allowed transition buttons per current status
// - clicking a button fires the mutation with the right (signalId, status)
// - the mutation optimistically patches the feed-list + detail caches and
//   rolls back on error.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  type InfiniteData,
} from "@tanstack/react-query";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";

afterEach(cleanup);

// ---- Mock the transport so no real fetch happens ---------------------------

const changeSignalStatusMock = vi.fn();
vi.mock("@/lib/signals-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/signals-api")>();
  return {
    ...actual,
    changeSignalStatus: (...args: unknown[]) => changeSignalStatusMock(...args),
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

import { StatusControls } from "@/components/signals/status-controls";
import {
  useChangeSignalStatus,
  feedKeys,
} from "@/hooks/use-signals";
import type { FeedPage, SignalDetailRead } from "@/lib/signals-api";

// ---- Wrappers --------------------------------------------------------------

function makeClient() {
  return new QueryClient({
    defaultOptions: {
      // Non-zero gcTime: the optimistic-update tests seed cache entries via
      // setQueryData with no active observer, so gcTime:0 would garbage-collect
      // them before we can assert on the optimistic write.
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

// ---- Fixtures --------------------------------------------------------------

const NOW_ISO = "2026-05-22T12:00:00Z";

function feedPage(signalId: string, status: FeedPage["data"][number]["status"]): FeedPage {
  return {
    data: [
      {
        score_id: "score-1",
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
        score: 80,
        status,
        score_breakdown: {},
        matched_keywords: [],
        created_at: NOW_ISO,
      },
    ],
    page: { next_cursor: null, has_more: false, limit: 25 },
  };
}

function infinite(page: FeedPage): InfiniteData<FeedPage> {
  return { pages: [page], pageParams: [undefined] };
}

// ---- Component tests -------------------------------------------------------

describe("StatusControls — renders allowed actions per status", () => {
  it("from new: shows Mark reviewed, Pin, Dismiss (not Restore)", () => {
    const client = makeClient();
    render(<StatusControls signalId="sig-1" status="new" />, {
      wrapper: wrapperFor(client),
    });
    expect(screen.getByTestId("status-action-reviewed")).toBeTruthy();
    expect(screen.getByTestId("status-action-pinned")).toBeTruthy();
    expect(screen.getByTestId("status-action-dismissed")).toBeTruthy();
    expect(screen.queryByTestId("status-action-new")).toBeNull();
  });

  it("from pinned: shows Mark reviewed + Dismiss (no Pin)", () => {
    const client = makeClient();
    render(<StatusControls signalId="sig-1" status="pinned" />, {
      wrapper: wrapperFor(client),
    });
    expect(screen.getByTestId("status-action-reviewed")).toBeTruthy();
    expect(screen.getByTestId("status-action-dismissed")).toBeTruthy();
    expect(screen.queryByTestId("status-action-pinned")).toBeNull();
  });

  it("from dismissed: shows only Restore (-> new)", () => {
    const client = makeClient();
    render(<StatusControls signalId="sig-1" status="dismissed" />, {
      wrapper: wrapperFor(client),
    });
    const restore = screen.getByTestId("status-action-new");
    expect(restore.textContent).toBe("Restore");
    expect(screen.queryByTestId("status-action-pinned")).toBeNull();
  });

  it("clicking Pin fires the mutation with (signalId, 'pinned')", async () => {
    changeSignalStatusMock.mockResolvedValueOnce({
      score_id: "score-1",
      signal_id: "sig-1",
      status: "pinned",
    });
    const client = makeClient();
    render(<StatusControls signalId="sig-1" status="new" />, {
      wrapper: wrapperFor(client),
    });
    fireEvent.click(screen.getByTestId("status-action-pinned"));
    await waitFor(() => {
      expect(changeSignalStatusMock).toHaveBeenCalledWith(
        "tok-123",
        "ws-1",
        "sig-1",
        "pinned",
      );
    });
  });
});

// ---- Hook optimistic-update tests ------------------------------------------

describe("useChangeSignalStatus — optimistic update", () => {
  it("optimistically patches the feed-list + detail caches before the request resolves", async () => {
    const client = makeClient();
    // Seed a feed list cache + a detail cache for the workspace.
    const listKey = feedKeys.list("ws-1", {});
    const detailKey = feedKeys.detail("ws-1", "sig-1");
    client.setQueryData(listKey, infinite(feedPage("sig-1", "new")));
    client.setQueryData<SignalDetailRead>(detailKey, {
      signal: feedPage("sig-1", "new").data[0].signal,
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
      feedback: null,
    });

    // Never-resolving request so we observe the optimistic state mid-flight.
    let resolve!: (v: unknown) => void;
    changeSignalStatusMock.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );

    const { result } = renderHook(() => useChangeSignalStatus(), {
      wrapper: wrapperFor(client),
    });

    act(() => {
      result.current.mutate({ signalId: "sig-1", status: "reviewed" });
    });

    // Optimistic write applied to both caches.
    await waitFor(() => {
      const list = client.getQueryData<InfiniteData<FeedPage>>(listKey);
      expect(list?.pages[0].data[0].status).toBe("reviewed");
      const detail = client.getQueryData<SignalDetailRead>(detailKey);
      expect(detail?.status).toBe("reviewed");
    });

    // Let it settle (resolve the request).
    act(() => {
      resolve({ score_id: "score-1", signal_id: "sig-1", status: "reviewed" });
    });
  });

  it("rolls the caches back to the snapshot on error", async () => {
    const client = makeClient();
    const listKey = feedKeys.list("ws-1", {});
    client.setQueryData(listKey, infinite(feedPage("sig-1", "new")));

    changeSignalStatusMock.mockRejectedValueOnce(new Error("boom"));

    const { result } = renderHook(() => useChangeSignalStatus(), {
      wrapper: wrapperFor(client),
    });

    await act(async () => {
      await result.current
        .mutateAsync({ signalId: "sig-1", status: "reviewed" })
        .catch(() => undefined);
    });

    // After the error + rollback, the cached status is back to "new".
    await waitFor(() => {
      const list = client.getQueryData<InfiniteData<FeedPage>>(listKey);
      expect(list?.pages[0].data[0].status).toBe("new");
    });
  });
});
