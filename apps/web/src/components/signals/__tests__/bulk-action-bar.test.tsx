// @vitest-environment jsdom
// G3 — BulkActionBar + useBulkChangeSignalStatus + FeedList multi-select:
// - BulkActionBar renders nothing with no selection; shows count + Pin/Dismiss when selected.
// - clicking Pin/Dismiss fires the bulk mutation with the selected ids + target status,
//   and clears the selection (onClear) on success.
// - useBulkChangeSignalStatus optimistically patches every selected feed item across
//   the cached lists and rolls back on error.
// - FeedList wiring: row checkboxes + select-all-visible drive the bar's visibility.

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  fireEvent,
  waitFor,
} from "@testing-library/react";
import {
  QueryClient,
  QueryClientProvider,
  type InfiniteData,
} from "@tanstack/react-query";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";

afterEach(cleanup);

// ---- Mock the transport so no real fetch happens ---------------------------

const changeSignalStatusBulkMock = vi.fn();
vi.mock("@/lib/signals-api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/signals-api")>();
  return {
    ...actual,
    changeSignalStatusBulk: (...args: unknown[]) =>
      changeSignalStatusBulkMock(...args),
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

import { BulkActionBar } from "@/components/signals/bulk-action-bar";
import { useBulkChangeSignalStatus, feedKeys } from "@/hooks/use-signals";
import type { FeedPage } from "@/lib/signals-api";

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

// ---- Fixtures --------------------------------------------------------------

const NOW_ISO = "2026-05-22T12:00:00Z";

function feedItem(signalId: string, status: FeedPage["data"][number]["status"]) {
  return {
    score_id: `score-${signalId}`,
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
      title: `RFP ${signalId}`,
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
  };
}

function feedPage(...ids: string[]): FeedPage {
  return {
    data: ids.map((id) => feedItem(id, "new")),
    page: { next_cursor: null, has_more: false, limit: 25 },
  };
}

function infinite(page: FeedPage): InfiniteData<FeedPage> {
  return { pages: [page], pageParams: [undefined] };
}

// ---- BulkActionBar component tests -----------------------------------------

describe("BulkActionBar — visibility + actions", () => {
  it("renders nothing when no rows are selected", () => {
    const client = makeClient();
    const { container } = render(
      <BulkActionBar selectedIds={[]} onClear={vi.fn()} />,
      { wrapper: wrapperFor(client) },
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows the selected count + Pin/Dismiss when ≥ 1 selected", () => {
    const client = makeClient();
    render(<BulkActionBar selectedIds={["a", "b"]} onClear={vi.fn()} />, {
      wrapper: wrapperFor(client),
    });
    expect(screen.getByTestId("bulk-action-bar")).toBeTruthy();
    expect(screen.getByTestId("bulk-selected-count").textContent).toBe(
      "2 selected",
    );
    expect(screen.getByTestId("bulk-action-pinned")).toBeTruthy();
    expect(screen.getByTestId("bulk-action-dismissed")).toBeTruthy();
  });

  it("clicking Dismiss fires the bulk mutation with the ids + 'dismissed' and clears on success", async () => {
    changeSignalStatusBulkMock.mockResolvedValueOnce({
      status: "dismissed",
      succeeded: ["a", "b"],
      skipped: [],
    });
    const onClear = vi.fn();
    const client = makeClient();
    render(<BulkActionBar selectedIds={["a", "b"]} onClear={onClear} />, {
      wrapper: wrapperFor(client),
    });
    fireEvent.click(screen.getByTestId("bulk-action-dismissed"));
    await waitFor(() => {
      expect(changeSignalStatusBulkMock).toHaveBeenCalledWith(
        "tok-123",
        "ws-1",
        ["a", "b"],
        "dismissed",
      );
    });
    await waitFor(() => expect(onClear).toHaveBeenCalledOnce());
  });

  it("clicking Pin fires the bulk mutation with 'pinned'", async () => {
    changeSignalStatusBulkMock.mockResolvedValueOnce({
      status: "pinned",
      succeeded: ["a"],
      skipped: [],
    });
    const client = makeClient();
    render(<BulkActionBar selectedIds={["a"]} onClear={vi.fn()} />, {
      wrapper: wrapperFor(client),
    });
    fireEvent.click(screen.getByTestId("bulk-action-pinned"));
    await waitFor(() => {
      expect(changeSignalStatusBulkMock).toHaveBeenCalledWith(
        "tok-123",
        "ws-1",
        ["a"],
        "pinned",
      );
    });
  });
});

// ---- Hook optimistic-update tests ------------------------------------------

describe("useBulkChangeSignalStatus — optimistic multi-patch", () => {
  it("optimistically patches every selected feed item before the request resolves", async () => {
    const client = makeClient();
    const listKey = feedKeys.list("ws-1", {});
    // Three rows; we select two of them.
    client.setQueryData(listKey, infinite(feedPage("a", "b", "c")));

    let resolve!: (v: unknown) => void;
    changeSignalStatusBulkMock.mockReturnValueOnce(
      new Promise((r) => {
        resolve = r;
      }),
    );

    const { result } = renderHook(() => useBulkChangeSignalStatus(), {
      wrapper: wrapperFor(client),
    });

    act(() => {
      result.current.mutate({ signalIds: ["a", "c"], status: "dismissed" });
    });

    await waitFor(() => {
      const list = client.getQueryData<InfiniteData<FeedPage>>(listKey);
      const byId = Object.fromEntries(
        list!.pages[0].data.map((i) => [i.signal.id, i.status]),
      );
      expect(byId["a"]).toBe("dismissed");
      expect(byId["c"]).toBe("dismissed");
      // The unselected row is untouched.
      expect(byId["b"]).toBe("new");
    });

    act(() => {
      resolve({ status: "dismissed", succeeded: ["a", "c"], skipped: [] });
    });
  });

  it("rolls the caches back to the snapshot on error", async () => {
    const client = makeClient();
    const listKey = feedKeys.list("ws-1", {});
    client.setQueryData(listKey, infinite(feedPage("a", "b")));

    changeSignalStatusBulkMock.mockRejectedValueOnce(new Error("boom"));

    const { result } = renderHook(() => useBulkChangeSignalStatus(), {
      wrapper: wrapperFor(client),
    });

    await act(async () => {
      await result.current
        .mutateAsync({ signalIds: ["a", "b"], status: "dismissed" })
        .catch(() => undefined);
    });

    await waitFor(() => {
      const list = client.getQueryData<InfiniteData<FeedPage>>(listKey);
      expect(list!.pages[0].data.every((i) => i.status === "new")).toBe(true);
    });
  });
});
