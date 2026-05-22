// Pipeline hooks (J5, J4, J2) — TanStack Query owns server state (doc 06 §2).
//
// usePipelineReport      — fetch the workspace-level pipeline rollup report (J5).
// useCreatePipelineItem  — mutation: create a manual pipeline item (J4).
// usePipelineStages      — fetch the workspace's stages, ordered (J2 columns).
// usePipelineItems       — fetch the workspace's items (J2 cards).
// useMovePipelineItem    — mutation: move an item between stages with optimistic
//                          update + rollback + conflict reconciliation (J2).
//
// All hooks are workspace-scoped: they skip when token or workspaceId are absent.

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createManualPipelineItem,
  getPipelineReport,
  listPipelineItems,
  listPipelineStages,
  movePipelineItem,
  type ManualPipelineItemCreate,
  type MoveItemInput,
  type PipelineItem,
  type PipelineItemPage,
  type PipelineReport,
  type PipelineStagePage,
} from "@/lib/pipeline-api";
import { ProblemError } from "@/lib/auth-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

// ---- Auth/workspace helpers ----

function useAuth() {
  const token = useSessionStore((s) => s.accessToken);
  const workspaceId = useUiStore((s) => s.activeWorkspaceId);
  return { token, workspaceId };
}

// ---- Query keys ----

export const pipelineKeys = {
  all: ["pipeline"] as const,
  report: (workspaceId: string) =>
    [...pipelineKeys.all, "report", workspaceId] as const,
  items: (workspaceId: string) =>
    [...pipelineKeys.all, "items", workspaceId] as const,
  stages: (workspaceId: string) =>
    [...pipelineKeys.all, "stages", workspaceId] as const,
};

// ---- usePipelineReport ----

/**
 * Fetch the workspace-level pipeline rollup report.
 * Returns per-stage item counts + summed value_estimate, plus workspace totals.
 * Skipped when token or workspaceId are absent.
 */
export function usePipelineReport(
  token: string | null,
  workspaceId: string | null,
) {
  return useQuery<PipelineReport>({
    queryKey: pipelineKeys.report(workspaceId ?? ""),
    queryFn: () => getPipelineReport(token!, workspaceId!),
    enabled: token !== null && workspaceId !== null,
  });
}

// ---- useCreatePipelineItem ----

/**
 * Create a manual pipeline item (J4).
 * Calls POST /pipeline/items/manual and invalidates the items list and report
 * on success so any list views stay current.
 */
export function useCreatePipelineItem() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<PipelineItem, Error, ManualPipelineItemCreate>({
    mutationFn: (input) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return createManualPipelineItem(token, workspaceId, input);
    },
    onSuccess: () => {
      // Invalidate items list and report so they refetch with the new item.
      void queryClient.invalidateQueries({
        queryKey: pipelineKeys.items(workspaceId ?? ""),
      });
      void queryClient.invalidateQueries({
        queryKey: pipelineKeys.report(workspaceId ?? ""),
      });
    },
  });
}

// ---- usePipelineStages ----

/**
 * Fetch the workspace's pipeline stages (J2 Kanban columns), ordered by
 * position. Skipped when token or workspaceId are absent.
 */
export function usePipelineStages(
  token: string | null,
  workspaceId: string | null,
) {
  return useQuery<PipelineStagePage>({
    queryKey: pipelineKeys.stages(workspaceId ?? ""),
    queryFn: () => listPipelineStages(token!, workspaceId!),
    enabled: token !== null && workspaceId !== null,
  });
}

// ---- usePipelineItems ----

/**
 * Fetch the workspace's pipeline items (J2 Kanban cards). Skipped when token or
 * workspaceId are absent.
 *
 * NOTE: this fetches a single page (limit 100). For very large pipelines the
 * board should follow ``next_cursor`` and accumulate pages.
 * // TODO J2: paginate columns once a workspace exceeds ~100 open items.
 */
export function usePipelineItems(
  token: string | null,
  workspaceId: string | null,
) {
  return useQuery<PipelineItemPage>({
    queryKey: pipelineKeys.items(workspaceId ?? ""),
    queryFn: () => listPipelineItems(token!, workspaceId!),
    enabled: token !== null && workspaceId !== null,
  });
}

// ---- useMovePipelineItem ----

export interface MovePipelineItemVars {
  itemId: string;
  /** The stage the card is being dropped into. */
  toStageId: string;
  /**
   * The stage the card was in when the drag started, captured from the cached
   * board snapshot. Used for optimistic-concurrency conflict detection: if the
   * server's current stage no longer matches this, someone else moved the item
   * first and we reconcile instead of clobbering.
   */
  expectedFromStageId: string;
  status?: MoveItemInput["status"];
}

/** Discriminates why a move failed so the UI can react appropriately. */
export class PipelineMoveConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PipelineMoveConflictError";
  }
}

/**
 * Move a pipeline item to a different stage (J2 Kanban DnD) with an optimistic
 * cache update, rollback on error, and conflict reconciliation.
 *
 * Optimistic update (TanStack Query, doc 06 §2):
 *   onMutate  — cancel in-flight item refetches, snapshot the items cache, and
 *               immediately rewrite the dragged item's ``stage_id`` so the card
 *               jumps columns before the network round-trip.
 *   onError   — restore the snapshot (rollback) so a failed move snaps the card
 *               back to its origin column.
 *   onSettled — invalidate items + report so the board reconciles with the
 *               server (also resolves any conflict by refetching truth).
 *
 * Conflict resolution:
 *   The move endpoint does NOT (yet) accept an expected-stage/version param for
 *   true server-side optimistic concurrency. We approximate OCC client-side:
 *   ``onMutate`` checks the *cached* current stage against ``expectedFromStageId``.
 *   If they already differ, the local cache is stale (the item moved out from
 *   under us — e.g. a teammate dragged it, or our own earlier move is still in
 *   flight); we throw ``PipelineMoveConflictError`` BEFORE issuing the request so
 *   we never clobber a newer server state. A 404/409 from the server is likewise
 *   treated as a conflict. Either way ``onSettled`` refetches, so the board
 *   reconciles non-destructively and the UI surfaces a transient message rather
 *   than silently overwriting.
 *
 *   // TODO J2: when the backend gains an ``expected_stage_id``/``version`` field
 *   // on POST /pipeline/items/{id}/move, send it for true server-side OCC and
 *   // drop the client-side stale-cache pre-check above.
 */
export function useMovePipelineItem() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();
  const itemsKey = pipelineKeys.items(workspaceId ?? "");
  const reportKey = pipelineKeys.report(workspaceId ?? "");

  return useMutation<
    PipelineItem,
    Error,
    MovePipelineItemVars,
    { previous: PipelineItemPage | undefined }
  >({
    mutationFn: async (vars) => {
      if (!token || !workspaceId)
        throw new Error("not authenticated");
      try {
        return await movePipelineItem(token, workspaceId, vars.itemId, {
          stage_id: vars.toStageId,
          ...(vars.status ? { status: vars.status } : {}),
        });
      } catch (err) {
        // Translate a 404 (item/stage vanished) or 409 (server-side conflict)
        // into our conflict type so onError/the UI can reconcile rather than
        // showing a raw error. Other problems propagate unchanged.
        if (
          err instanceof ProblemError &&
          (err.problem.status === 404 || err.problem.status === 409)
        ) {
          throw new PipelineMoveConflictError(
            "This item changed since the board loaded — refreshing.",
          );
        }
        throw err;
      }
    },
    onMutate: async (vars) => {
      // Stop in-flight item refetches so they don't overwrite our optimistic
      // write between snapshot and settle.
      await queryClient.cancelQueries({ queryKey: itemsKey });
      const previous = queryClient.getQueryData<PipelineItemPage>(itemsKey);

      // Client-side stale-cache check (approximate OCC — see docstring).
      const cached = previous?.items.find((i) => i.id === vars.itemId);
      if (cached && cached.stage_id !== vars.expectedFromStageId) {
        throw new PipelineMoveConflictError(
          "This item was moved by someone else — refreshing the board.",
        );
      }

      // Optimistically move the card to the target column.
      if (previous) {
        queryClient.setQueryData<PipelineItemPage>(itemsKey, {
          ...previous,
          items: previous.items.map((i) =>
            i.id === vars.itemId ? { ...i, stage_id: vars.toStageId } : i,
          ),
        });
      }
      return { previous };
    },
    onError: (_err, _vars, context) => {
      // Roll back to the pre-move snapshot.
      if (context?.previous !== undefined) {
        queryClient.setQueryData<PipelineItemPage>(itemsKey, context.previous);
      }
    },
    onSettled: () => {
      // Reconcile with the server regardless of success/failure. On a conflict
      // this is what surfaces the authoritative state non-destructively.
      void queryClient.invalidateQueries({ queryKey: itemsKey });
      void queryClient.invalidateQueries({ queryKey: reportKey });
    },
  });
}
