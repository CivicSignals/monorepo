// KanbanBoard — drag-and-drop pipeline board (J2).
//
// Renders one column per pipeline stage and a draggable card per pipeline item.
// Dragging a card to another column calls POST /pipeline/items/{id}/move via the
// useMovePipelineItem mutation, which applies an OPTIMISTIC cache update (the card
// jumps columns before the round-trip), rolls back on error, and reconciles with
// the server on settle (see src/hooks/use-pipeline.ts for the OCC story).
//
// State split (doc 06 §2): all server state (stages, items) comes from TanStack
// Query; the only client-only state here is the transient conflict/error banner
// and the active-drag id, both ephemeral React state (not Zustand).
//
// Accessibility: @dnd-kit ships a KeyboardSensor, so cards are focusable and can
// be picked up / moved between columns with the keyboard (space to grab, arrows
// to move, space to drop). Each draggable exposes an aria-label naming the card.

"use client";

import { useMemo, useState } from "react";
import {
  DndContext,
  DragOverlay,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import {
  PipelineMoveConflictError,
  useMovePipelineItem,
  usePipelineItems,
  usePipelineStages,
} from "@/hooks/use-pipeline";
import { ProblemError } from "@/lib/auth-api";
import type { PipelineItem, PipelineStage } from "@/lib/pipeline-api";

// ---- Helpers ----------------------------------------------------------------

function formatUSD(value: string | null): string {
  if (value === null) return "";
  const num = parseFloat(value);
  if (Number.isNaN(num)) return "";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(num);
}

// Map a Won/Lost stage name to a status update so a drag also closes the deal.
// Kept intentionally simple/name-based — the backend move endpoint accepts an
// optional status, so this is a UX nicety, not a contract requirement.
function statusForStage(stage: PipelineStage): PipelineItem["status"] | undefined {
  const name = stage.name.trim().toLowerCase();
  if (name === "won") return "won";
  if (name === "lost") return "lost";
  if (name === "disqualified") return "disqualified";
  return undefined;
}

// ---- Card -------------------------------------------------------------------

function ItemCard({ item }: { item: PipelineItem }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } =
    useDraggable({ id: item.id, data: { stageId: item.stage_id } });

  const style = {
    transform: CSS.Translate.toString(transform),
    opacity: isDragging ? 0.4 : undefined,
  };

  const value = formatUSD(item.value_estimate);

  return (
    <div
      ref={setNodeRef}
      style={style}
      data-testid={`card-${item.id}`}
      data-stage-id={item.stage_id}
      aria-label={`Pipeline item: ${item.title}`}
      className="cursor-grab touch-none rounded-md border border-gray-200 bg-white p-3 shadow-sm hover:border-indigo-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 active:cursor-grabbing"
      {...listeners}
      {...attributes}
    >
      <p className="text-sm font-medium text-gray-900">{item.title}</p>
      {value && (
        <p className="mt-1 text-xs font-medium tabular-nums text-gray-500">
          {value}
        </p>
      )}
    </div>
  );
}

// A non-interactive clone shown under the cursor while dragging.
function CardPreview({ item }: { item: PipelineItem }) {
  const value = formatUSD(item.value_estimate);
  return (
    <div className="cursor-grabbing rounded-md border border-indigo-400 bg-white p-3 shadow-lg">
      <p className="text-sm font-medium text-gray-900">{item.title}</p>
      {value && (
        <p className="mt-1 text-xs font-medium tabular-nums text-gray-500">
          {value}
        </p>
      )}
    </div>
  );
}

// ---- Column -----------------------------------------------------------------

function StageColumn({
  stage,
  items,
}: {
  stage: PipelineStage;
  items: PipelineItem[];
}) {
  const { setNodeRef, isOver } = useDroppable({ id: stage.id });

  return (
    <div
      ref={setNodeRef}
      data-testid={`column-${stage.id}`}
      aria-label={`Stage: ${stage.name}`}
      className={`flex w-64 flex-shrink-0 flex-col rounded-lg border p-2 transition-colors ${
        isOver ? "border-indigo-400 bg-indigo-50" : "border-gray-200 bg-gray-50"
      }`}
    >
      <div className="mb-2 flex items-center justify-between px-1">
        <h3 className="text-sm font-semibold text-gray-700">{stage.name}</h3>
        <span className="rounded-full bg-gray-200 px-2 py-0.5 text-xs font-medium tabular-nums text-gray-600">
          {items.length}
        </span>
      </div>
      <div className="flex flex-col gap-2 min-h-12">
        {items.map((item) => (
          <ItemCard key={item.id} item={item} />
        ))}
        {items.length === 0 && (
          <p className="px-1 py-2 text-xs italic text-gray-400">No items</p>
        )}
      </div>
    </div>
  );
}

// ---- Board ------------------------------------------------------------------

interface KanbanBoardProps {
  token: string | null;
  workspaceId: string | null;
}

export function KanbanBoard({ token, workspaceId }: KanbanBoardProps) {
  const stagesQuery = usePipelineStages(token, workspaceId);
  const itemsQuery = usePipelineItems(token, workspaceId);
  const moveMutation = useMovePipelineItem();

  // Transient banner shown after a non-destructive conflict reconcile (J2).
  const [conflictMessage, setConflictMessage] = useState<string | null>(null);
  // The id of the card currently being dragged (for the DragOverlay preview).
  const [activeId, setActiveId] = useState<string | null>(null);

  // dnd-kit sensors: pointer for mouse/touch, keyboard for a11y.
  const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor));

  const stages = useMemo(
    () => [...(stagesQuery.data?.items ?? [])].sort((a, b) => a.position - b.position),
    [stagesQuery.data],
  );
  const items = useMemo(() => itemsQuery.data?.items ?? [], [itemsQuery.data]);

  // Group items by stage for O(1) column rendering.
  const itemsByStage = useMemo(() => {
    const map = new Map<string, PipelineItem[]>();
    for (const item of items) {
      const bucket = map.get(item.stage_id);
      if (bucket) bucket.push(item);
      else map.set(item.stage_id, [item]);
    }
    return map;
  }, [items]);

  const activeItem = activeId
    ? (items.find((i) => i.id === activeId) ?? null)
    : null;

  function handleDragStart(event: DragStartEvent) {
    setActiveId(String(event.active.id));
  }

  function handleDragEnd(event: DragEndEvent) {
    setActiveId(null);
    const { active, over } = event;
    if (!over) return; // dropped outside any column

    const itemId = String(active.id);
    const toStageId = String(over.id);
    // The stage the card started in, captured from the draggable's data so the
    // mutation can detect a stale cache (approximate OCC — see use-pipeline.ts).
    const fromStageId = String(active.data.current?.stageId ?? "");
    if (!fromStageId || toStageId === fromStageId) return; // no-op

    const targetStage = stages.find((s) => s.id === toStageId);
    const status = targetStage ? statusForStage(targetStage) : undefined;

    moveMutation.mutate(
      {
        itemId,
        toStageId,
        expectedFromStageId: fromStageId,
        ...(status ? { status } : {}),
      },
      {
        onError: (err) => {
          // Conflicts (stale cache / 404 / 409) are reconciled non-destructively
          // by onSettled's refetch; surface a transient, friendly banner. Other
          // errors (network, 5xx) get a generic message — the rollback already
          // restored the card to its origin column.
          if (err instanceof PipelineMoveConflictError) {
            setConflictMessage(err.message);
          } else if (err instanceof ProblemError) {
            setConflictMessage(
              err.problem.detail ?? err.problem.title ?? "Could not move item.",
            );
          } else {
            setConflictMessage("Could not move item — please try again.");
          }
        },
        onSuccess: () => setConflictMessage(null),
      },
    );
  }

  // ---- Render states --------------------------------------------------------

  if (stagesQuery.isLoading || itemsQuery.isLoading) {
    return (
      <div
        role="status"
        aria-label="Loading pipeline board"
        className="flex gap-4 overflow-x-auto pb-2"
      >
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-64 w-64 flex-shrink-0 animate-pulse rounded-lg bg-gray-100" />
        ))}
      </div>
    );
  }

  if (stagesQuery.isError || itemsQuery.isError) {
    const err = stagesQuery.error ?? itemsQuery.error;
    return (
      <div
        role="alert"
        className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700"
      >
        Failed to load the pipeline board:{" "}
        {err instanceof Error ? err.message : "Unknown error"}
      </div>
    );
  }

  if (stages.length === 0) {
    return (
      <p className="text-sm italic text-gray-400">No stages configured.</p>
    );
  }

  return (
    <div className="space-y-3">
      {conflictMessage && (
        <div
          role="status"
          className="flex items-center justify-between rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800"
        >
          <span>{conflictMessage}</span>
          <button
            type="button"
            onClick={() => setConflictMessage(null)}
            aria-label="Dismiss message"
            className="ml-4 rounded p-1 text-amber-700 hover:bg-amber-100"
          >
            &#x2715;
          </button>
        </div>
      )}

      <DndContext
        sensors={sensors}
        collisionDetection={closestCorners}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
        onDragCancel={() => setActiveId(null)}
      >
        <div
          aria-label="Pipeline Kanban board"
          className="flex gap-4 overflow-x-auto pb-2"
        >
          {stages.map((stage) => (
            <StageColumn
              key={stage.id}
              stage={stage}
              items={itemsByStage.get(stage.id) ?? []}
            />
          ))}
        </div>
        <DragOverlay>
          {activeItem ? <CardPreview item={activeItem} /> : null}
        </DragOverlay>
      </DndContext>
    </div>
  );
}
