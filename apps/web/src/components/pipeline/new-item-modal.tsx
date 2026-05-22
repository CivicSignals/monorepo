// NewItemModal — dialog for manually creating a pipeline item (J4).
//
// Opens as a modal dialog; the trigger is a "New item" button on the pipeline
// page. Collects title (required), optional stage, value estimate, and notes.
// Submits via useCreatePipelineItem (TanStack Query mutation). On success, the
// items list and pipeline report are invalidated automatically by the hook.
//
// State: all form state is local React state — purely client-side UI, no Zustand
// (doc 06 §2: "Zustand owns client-only state; TanStack Query owns server state").

"use client";

import { useEffect, useState } from "react";
import { useCreatePipelineItem } from "@/hooks/use-pipeline";
import { ProblemError } from "@/lib/auth-api";

interface NewItemModalProps {
  open: boolean;
  onClose: () => void;
}

export function NewItemModal({ open, onClose }: NewItemModalProps) {
  // Close on Escape key.
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Add pipeline item"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
    >
      <div className="w-full max-w-lg rounded-xl border bg-background p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">New pipeline item</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="rounded-md p-1 text-muted-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"
          >
            &#x2715;
          </button>
        </div>
        <NewItemForm onClose={onClose} />
      </div>
    </div>
  );
}

// ---- Form ----

interface NewItemFormProps {
  onClose: () => void;
}

function NewItemForm({ onClose }: NewItemFormProps) {
  const createMutation = useCreatePipelineItem();

  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [valueEstimate, setValueEstimate] = useState("");

  const isValid = title.trim().length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid) return;

    try {
      await createMutation.mutateAsync({
        title: title.trim(),
        ...(notes.trim() ? { notes: notes.trim() } : {}),
        ...(valueEstimate.trim()
          ? { value_estimate: valueEstimate.trim() }
          : {}),
      });
      onClose();
    } catch {
      // Error displayed via createMutation.error below.
    }
  };

  const errorMessage = createMutation.error
    ? createMutation.error instanceof ProblemError
      ? (createMutation.error.problem.detail ??
        createMutation.error.problem.title)
      : createMutation.error.message
    : null;

  return (
    <form
      onSubmit={(e) => void handleSubmit(e)}
      className="space-y-4"
      aria-label="New pipeline item form"
    >
      {/* Title — required */}
      <div>
        <label
          htmlFor="new-item-title"
          className="block text-sm font-medium text-foreground"
        >
          Title <span aria-hidden>*</span>
        </label>
        <input
          id="new-item-title"
          type="text"
          required
          maxLength={500}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Opportunity name or short description"
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {/* Value estimate — optional */}
      <div>
        <label
          htmlFor="new-item-value"
          className="block text-sm font-medium text-foreground"
        >
          Estimated value (USD)
        </label>
        <input
          id="new-item-value"
          type="number"
          min="0"
          step="0.01"
          value={valueEstimate}
          onChange={(e) => setValueEstimate(e.target.value)}
          placeholder="0.00"
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {/* Notes — optional */}
      <div>
        <label
          htmlFor="new-item-notes"
          className="block text-sm font-medium text-foreground"
        >
          Notes
        </label>
        <textarea
          id="new-item-notes"
          rows={3}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Context, next steps, or any notes about this opportunity…"
          className="mt-1 w-full rounded-md border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </div>

      {/* Server error */}
      {errorMessage && (
        <div
          role="alert"
          className="rounded-md border border-destructive/50 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {errorMessage}
        </div>
      )}

      {/* Actions */}
      <div className="flex justify-end gap-3 pt-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-md px-4 py-2 text-sm font-medium text-muted-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={!isValid || createMutation.isPending}
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
        >
          {createMutation.isPending ? "Adding…" : "Add item"}
        </button>
      </div>
    </form>
  );
}
