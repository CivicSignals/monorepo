// @vitest-environment jsdom
// J2 — Kanban board + move mutation.
//
// Two layers of coverage:
//   1. KanbanBoard render — columns come from stages, cards from items, counts
//      reflect grouping, empty/loading states.
//   2. useMovePipelineItem — the optimistic-update / rollback / conflict logic
//      that the drag-end handler drives. Simulating a real @dnd-kit pointer drag
//      in jsdom is brittle, so we exercise the mutation directly (it is the unit
//      that carries the J2 behaviour: POST /pipeline/items/{id}/move, optimistic
//      cache write, rollback on error, conflict reconciliation).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, renderHook, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { KanbanBoard } from "@/components/pipeline/kanban-board";
import {
  PipelineMoveConflictError,
  pipelineKeys,
  useMovePipelineItem,
} from "@/hooks/use-pipeline";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";
import type {
  PipelineItem,
  PipelineItemPage,
  PipelineStage,
  PipelineStagePage,
} from "@/lib/pipeline-api";

const WS = "ws-001";
const STAGE_A = "00000000-0000-7000-8000-00000000000a";
const STAGE_B = "00000000-0000-7000-8000-00000000000b";

// ---- Fixtures ---------------------------------------------------------------

function makeStage(over: Partial<PipelineStage> = {}): PipelineStage {
  return {
    id: STAGE_A,
    workspace_id: WS,
    name: "Saved",
    position: 0,
    is_default: true,
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...over,
  };
}

function makeItem(over: Partial<PipelineItem> = {}): PipelineItem {
  return {
    id: "item-001",
    workspace_id: WS,
    stage_id: STAGE_A,
    signal_id: null,
    owner_id: null,
    title: "City Hall contract",
    notes: null,
    value_estimate: null,
    status: "active",
    created_at: "2026-05-22T00:00:00Z",
    updated_at: "2026-05-22T00:00:00Z",
    ...over,
  };
}

const STAGES: PipelineStagePage = {
  items: [
    makeStage({ id: STAGE_A, name: "Saved", position: 0 }),
    makeStage({ id: STAGE_B, name: "Researching", position: 1, is_default: false }),
  ],
  next_cursor: null,
};

const ITEMS: PipelineItemPage = {
  items: [
    makeItem({ id: "item-001", title: "City Hall contract", stage_id: STAGE_A }),
    makeItem({ id: "item-002", title: "School board RFP", stage_id: STAGE_B }),
  ],
  next_cursor: null,
};

// Route the transport's fetch by URL so the board's two queries resolve.
function mockBoardFetch() {
  return vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
    const url = String(input);
    if (url.includes("/pipeline/stages")) {
      return Promise.resolve(
        new Response(JSON.stringify(STAGES), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    if (url.includes("/pipeline/items")) {
      return Promise.resolve(
        new Response(JSON.stringify(ITEMS), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    return Promise.reject(new Error(`unexpected fetch: ${url}`));
  });
}

function newClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function makeWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

beforeEach(() => {
  localStorage.clear();
  useSessionStore.setState({ accessToken: "test-jwt", user: null });
  useUiStore.setState({ activeWorkspaceId: WS });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useSessionStore.setState({ accessToken: null, user: null });
  useUiStore.setState({ activeWorkspaceId: null });
});

// ---- Render -----------------------------------------------------------------

describe("KanbanBoard — render", () => {
  it("renders one column per stage", async () => {
    mockBoardFetch();
    const client = newClient();
    render(<KanbanBoard token="test-jwt" workspaceId={WS} />, {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => {
      expect(screen.getByTestId(`column-${STAGE_A}`)).toBeTruthy();
      expect(screen.getByTestId(`column-${STAGE_B}`)).toBeTruthy();
    });
    expect(screen.getByLabelText("Pipeline Kanban board")).toBeTruthy();
  });

  it("renders a draggable card per item in the correct column", async () => {
    mockBoardFetch();
    const client = newClient();
    render(<KanbanBoard token="test-jwt" workspaceId={WS} />, {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(screen.getByTestId("card-item-001")).toBeTruthy());
    // item-001 lives in Saved (STAGE_A); item-002 in Researching (STAGE_B).
    const colA = screen.getByTestId(`column-${STAGE_A}`);
    const colB = screen.getByTestId(`column-${STAGE_B}`);
    expect(colA.querySelector('[data-testid="card-item-001"]')).toBeTruthy();
    expect(colB.querySelector('[data-testid="card-item-002"]')).toBeTruthy();
  });

  it("shows a per-column item count", async () => {
    mockBoardFetch();
    const client = newClient();
    render(<KanbanBoard token="test-jwt" workspaceId={WS} />, {
      wrapper: makeWrapper(client),
    });

    await waitFor(() => expect(screen.getByTestId("card-item-001")).toBeTruthy());
    const colA = screen.getByTestId(`column-${STAGE_A}`);
    // Saved has exactly one item.
    expect(colA.textContent).toContain("1");
  });
});

// ---- Move mutation: optimistic + rollback + conflict ------------------------

describe("useMovePipelineItem — optimistic move", () => {
  it("POSTs to /pipeline/items/{id}/move and optimistically updates the cache", async () => {
    const client = newClient();
    // Seed the items cache as the board would have.
    client.setQueryData<PipelineItemPage>(pipelineKeys.items(WS), ITEMS);

    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify(makeItem({ id: "item-001", stage_id: STAGE_B })),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const { result } = renderHook(() => useMovePipelineItem(), {
      wrapper: makeWrapper(client),
    });

    await act(async () => {
      await result.current.mutateAsync({
        itemId: "item-001",
        toStageId: STAGE_B,
        expectedFromStageId: STAGE_A,
      });
    });

    // The move endpoint was called with a POST.
    const moveCall = fetchSpy.mock.calls.find(
      (c) =>
        String(c[0]).includes("/pipeline/items/item-001/move") &&
        (c[1] as RequestInit)?.method === "POST",
    );
    expect(moveCall).toBeTruthy();
    const body = JSON.parse((moveCall![1] as RequestInit).body as string) as {
      stage_id: string;
    };
    expect(body.stage_id).toBe(STAGE_B);

    // After settle, the cached item reflects the new stage.
    const cached = client.getQueryData<PipelineItemPage>(pipelineKeys.items(WS));
    const moved = cached?.items.find((i) => i.id === "item-001");
    expect(moved?.stage_id).toBe(STAGE_B);
  });

  it("rolls the card back to its origin column when the server errors", async () => {
    const client = newClient();
    client.setQueryData<PipelineItemPage>(pipelineKeys.items(WS), ITEMS);

    // A 500 server error (NOT a 404/409 conflict) → rollback path.
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ type: "about:blank", title: "Server error", status: 500 }),
        { status: 500, headers: { "Content-Type": "application/problem+json" } },
      ),
    );

    const { result } = renderHook(() => useMovePipelineItem(), {
      wrapper: makeWrapper(client),
    });

    await act(async () => {
      await result.current
        .mutateAsync({
          itemId: "item-001",
          toStageId: STAGE_B,
          expectedFromStageId: STAGE_A,
        })
        .catch(() => {
          // expected — error is asserted via cache state below.
        });
    });

    // onError restored the snapshot: item-001 is back in its original stage.
    const cached = client.getQueryData<PipelineItemPage>(pipelineKeys.items(WS));
    const item = cached?.items.find((i) => i.id === "item-001");
    expect(item?.stage_id).toBe(STAGE_A);
  });

  it("raises a conflict (without calling the server) when the cache is stale", async () => {
    const client = newClient();
    // Cache says item-001 is already in STAGE_B (someone moved it), but the drag
    // was started believing it was in STAGE_A → stale-cache conflict pre-check.
    client.setQueryData<PipelineItemPage>(pipelineKeys.items(WS), {
      items: [makeItem({ id: "item-001", stage_id: STAGE_B })],
      next_cursor: null,
    });

    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const { result } = renderHook(() => useMovePipelineItem(), {
      wrapper: makeWrapper(client),
    });

    let caught: unknown;
    await act(async () => {
      await result.current
        .mutateAsync({
          itemId: "item-001",
          toStageId: STAGE_A,
          expectedFromStageId: STAGE_A, // start stage no longer matches the cache
        })
        .catch((e) => {
          caught = e;
        });
    });

    expect(caught).toBeInstanceOf(PipelineMoveConflictError);
    // No move request was issued — we bailed before the network round-trip.
    expect(
      fetchSpy.mock.calls.some((c) => String(c[0]).includes("/move")),
    ).toBe(false);
  });

  it("maps a 409 server response to a conflict error", async () => {
    const client = newClient();
    client.setQueryData<PipelineItemPage>(pipelineKeys.items(WS), ITEMS);

    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({ type: "about:blank", title: "Conflict", status: 409 }),
        { status: 409, headers: { "Content-Type": "application/problem+json" } },
      ),
    );

    const { result } = renderHook(() => useMovePipelineItem(), {
      wrapper: makeWrapper(client),
    });

    let caught: unknown;
    await act(async () => {
      await result.current
        .mutateAsync({
          itemId: "item-001",
          toStageId: STAGE_B,
          expectedFromStageId: STAGE_A,
        })
        .catch((e) => {
          caught = e;
        });
    });

    expect(caught).toBeInstanceOf(PipelineMoveConflictError);
    // Rolled back to origin after the conflict.
    const cached = client.getQueryData<PipelineItemPage>(pipelineKeys.items(WS));
    expect(cached?.items.find((i) => i.id === "item-001")?.stage_id).toBe(STAGE_A);
  });
});
