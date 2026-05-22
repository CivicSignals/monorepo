// @vitest-environment jsdom
// G1 — FeedList: renders items from mocked data, filter interaction,
// empty state, error state, load-more visibility.

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import type { InfiniteData } from "@tanstack/react-query";

afterEach(cleanup);

// ---- Mock next/navigation (useRouter, useSearchParams) ---------------------

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

// ---- Mock the signals hook so we control data without a real API -----------

vi.mock("@/hooks/use-signals", () => ({
  useWorkspaceFeed: vi.fn(),
  feedKeys: {
    all: ["signals-feed"],
    workspace: (id: string | null) => ["signals-feed", id ?? "none"],
    list: (id: string | null, f: object) => ["signals-feed", id ?? "none", "list", f],
  },
}));

import { useWorkspaceFeed } from "@/hooks/use-signals";
import { FeedList } from "@/components/signals/feed-list";
import type { FeedPage } from "@/lib/signals-api";

// ---- Test wrapper ----------------------------------------------------------

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

// ---- Fixtures --------------------------------------------------------------

const NOW_ISO = "2026-05-22T12:00:00Z";

function makeItem(over: Partial<{ score: number; title: string; signal_type: string }> = {}) {
  const score = over.score ?? 75;
  const title = over.title ?? "RFP: K-8 Math Curriculum";
  const signal_type = over.signal_type ?? "rfp_posted";
  return {
    score_id: `score-${title.slice(0, 6)}`,
    signal: {
      id: `sig-${title.slice(0, 6)}`,
      entity_id: "ent-001",
      entity_name_raw: "Northshore School District",
      signal_type,
      recipe_id: "k12_rfp_v3",
      raw_document_ids: [],
      content_hash: "abc",
      occurred_at: NOW_ISO,
      observed_at: NOW_ISO,
      summary: "Summary for " + title,
      title,
      details: {},
      confidence: 0.9,
      status: "new",
      is_degraded: false,
      review_required: false,
      created_at: NOW_ISO,
    },
    score,
    status: "new" as const,
    score_breakdown: {},
    matched_keywords: [],
    created_at: NOW_ISO,
  };
}

function makePage(items: ReturnType<typeof makeItem>[], has_more = false): FeedPage {
  return {
    data: items,
    page: {
      next_cursor: has_more ? "cursor-xyz" : null,
      has_more,
      limit: 25,
    },
  };
}

function makeInfiniteData(pages: FeedPage[]): InfiniteData<FeedPage> {
  return {
    pages,
    pageParams: [undefined, ...pages.slice(1).map((_, i) => `cursor-${i}`)],
  };
}

// ---- Hook stub helpers -----------------------------------------------------

type MockReturn = ReturnType<typeof useWorkspaceFeed>;

function stubIdle(): Partial<MockReturn> {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  };
}

function stubLoading(): Partial<MockReturn> {
  return {
    data: undefined,
    isLoading: true,
    isError: false,
    error: null,
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  };
}

function stubError(msg = "Network error"): Partial<MockReturn> {
  return {
    data: undefined,
    isLoading: false,
    isError: true,
    error: new Error(msg),
    fetchNextPage: vi.fn(),
    hasNextPage: false,
    isFetchingNextPage: false,
  };
}

function stubLoaded(pages: FeedPage[], hasNextPage = false): Partial<MockReturn> {
  return {
    data: makeInfiniteData(pages),
    isLoading: false,
    isError: false,
    error: null,
    fetchNextPage: vi.fn(),
    hasNextPage,
    isFetchingNextPage: false,
  };
}

const mockUseWorkspaceFeed = vi.mocked(useWorkspaceFeed);

// ---- Tests -----------------------------------------------------------------

describe("FeedList — renders items from mocked data", () => {
  it("renders feed items", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([makeItem({ title: "RFP: K-8 Math" })])]) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    expect(screen.getByTestId("feed-list")).toBeTruthy();
    const items = screen.getAllByTestId("feed-item");
    expect(items.length).toBe(1);
    expect(screen.getByText("RFP: K-8 Math")).toBeTruthy();
  });

  it("renders signal type badge", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([makeItem({ signal_type: "rfp_posted" })])]) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    expect(screen.getByText("RFP Posted")).toBeTruthy();
  });

  it("renders score badge for a high-score item", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([makeItem({ score: 85 })])]) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    expect(screen.getByTestId("feed-item-score").textContent).toContain("High");
  });

  it("renders medium score band for score 55", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([makeItem({ score: 55 })])]) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    expect(screen.getByTestId("feed-item-score").textContent).toContain("Medium");
  });

  it("renders entity name when present", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([makeItem()])]) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    expect(screen.getByTestId("feed-item-entity").textContent).toBe(
      "Northshore School District",
    );
  });

  it("renders date from occurred_at", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([makeItem()])]) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    // May 22, 2026
    expect(screen.getByTestId("feed-item-date").textContent).toMatch(/May 22, 2026/);
  });

  it("renders multiple items across pages", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([
        makePage([makeItem({ title: "RFP A" }), makeItem({ title: "RFP B" })]),
      ]) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    const items = screen.getAllByTestId("feed-item");
    expect(items.length).toBe(2);
  });
});

describe("FeedList — loading state", () => {
  it("shows loading indicator when isLoading", () => {
    mockUseWorkspaceFeed.mockReturnValue(stubLoading() as MockReturn);
    render(<FeedList />, { wrapper });
    expect(screen.getByTestId("feed-loading")).toBeTruthy();
  });

  it("does not render feed-list while loading", () => {
    mockUseWorkspaceFeed.mockReturnValue(stubLoading() as MockReturn);
    render(<FeedList />, { wrapper });
    expect(screen.queryByTestId("feed-list")).toBeNull();
  });
});

describe("FeedList — error state", () => {
  it("shows error message on fetch failure", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubError("Service unavailable") as MockReturn,
    );
    render(<FeedList />, { wrapper });
    const err = screen.getByTestId("feed-error");
    expect(err.textContent).toContain("Service unavailable");
  });
});

describe("FeedList — empty state", () => {
  it("shows empty state when data has no items", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([])]) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    expect(screen.getByTestId("feed-empty")).toBeTruthy();
  });

  it("shows empty state when not authenticated (no data)", () => {
    mockUseWorkspaceFeed.mockReturnValue(stubIdle() as MockReturn);
    render(<FeedList />, { wrapper });
    // Neither loading nor error; items array is empty → empty state
    expect(screen.getByTestId("feed-empty")).toBeTruthy();
  });
});

describe("FeedList — filter interaction", () => {
  it("calls onFiltersChange when signal_type filter changes", () => {
    mockUseWorkspaceFeed.mockReturnValue(stubLoaded([makePage([])]) as MockReturn);
    const onChange = vi.fn();
    render(<FeedList filters={{}} onFiltersChange={onChange} />, { wrapper });
    const select = screen.getByTestId("filter-signal-type");
    fireEvent.change(select, { target: { value: "rfp_posted" } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ signal_type: "rfp_posted" }),
    );
  });

  it("calls onFiltersChange when status filter changes", () => {
    mockUseWorkspaceFeed.mockReturnValue(stubLoaded([makePage([])]) as MockReturn);
    const onChange = vi.fn();
    render(<FeedList filters={{}} onFiltersChange={onChange} />, { wrapper });
    const select = screen.getByTestId("filter-status");
    fireEvent.change(select, { target: { value: "reviewed" } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ status: "reviewed" }),
    );
  });

  it("calls onFiltersChange when min_score filter changes", () => {
    mockUseWorkspaceFeed.mockReturnValue(stubLoaded([makePage([])]) as MockReturn);
    const onChange = vi.fn();
    render(<FeedList filters={{}} onFiltersChange={onChange} />, { wrapper });
    const select = screen.getByTestId("filter-min-score");
    fireEvent.change(select, { target: { value: "80" } });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ min_score: 80 }),
    );
  });

  it("does not render filter bar when onFiltersChange is not provided", () => {
    mockUseWorkspaceFeed.mockReturnValue(stubLoaded([makePage([])]) as MockReturn);
    render(<FeedList />, { wrapper });
    expect(screen.queryByTestId("filter-signal-type")).toBeNull();
  });
});

describe("FeedList — pagination", () => {
  it("shows 'Load more' button when hasNextPage is true", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([makeItem()], true)], true) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    expect(screen.getByTestId("feed-load-more")).toBeTruthy();
  });

  it("hides 'Load more' when on last page", () => {
    mockUseWorkspaceFeed.mockReturnValue(
      stubLoaded([makePage([makeItem()], false)], false) as MockReturn,
    );
    render(<FeedList />, { wrapper });
    expect(screen.queryByTestId("feed-load-more")).toBeNull();
  });

  it("calls fetchNextPage when 'Load more' is clicked", () => {
    const fetchNextPage = vi.fn();
    mockUseWorkspaceFeed.mockReturnValue({
      ...(stubLoaded([makePage([makeItem()], true)], true) as MockReturn),
      fetchNextPage,
    } as MockReturn);
    render(<FeedList />, { wrapper });
    fireEvent.click(screen.getByTestId("feed-load-more"));
    expect(fetchNextPage).toHaveBeenCalledOnce();
  });
});
