"use client";

import { useEffect, useRef, useState } from "react";
import { Building2, Check, ChevronDown } from "lucide-react";
import { ProblemError } from "@/lib/auth-api";
import {
  useActiveWorkspace,
  useCreateWorkspace,
  useSwitchWorkspace,
  useWorkspaces,
} from "@/hooks/use-workspaces";

// Header workspace switcher + inline create flow (B5).
//
// Collapsed into a single dropdown so it occupies one nav slot instead of a
// wide <select> + button that crowded the header. The panel also explains what
// a workspace *is* — first-time users never had that context before.
//
// - Trigger shows the active workspace name (or "Workspaces" when none yet).
// - The menu lists the workspaces the user belongs to and switches the active
//   one (POST /workspaces/{id}/switch records last_active server-side).
// - An always-visible "Create New" field at the bottom creates a workspace and
//   makes it active.
// Server state is TanStack Query (useWorkspaces); the active selection is the
// Zustand UI slice surfaced via useActiveWorkspace.
export function WorkspaceSwitcher() {
  const { data, isLoading } = useWorkspaces();
  // Pass the fetched list in so we don't open a second query observer.
  const active = useActiveWorkspace(data);
  const switchWorkspace = useSwitchWorkspace();
  const createWorkspace = useCreateWorkspace();

  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  const workspaces = data ?? [];

  // Close the menu on outside click or Escape.
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const createError =
    createWorkspace.error instanceof ProblemError
      ? createWorkspace.error.problem.detail
      : createWorkspace.error?.message;

  function onCreate(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    // Use mutate (not mutateAsync) so a rejection isn't an unhandled promise:
    // errors surface via createWorkspace.error (rendered below), and the form
    // only resets on success.
    createWorkspace.mutate(
      { name: trimmed },
      {
        onSuccess: () => {
          setName("");
          setOpen(false);
        },
      },
    );
  }

  function onSelect(workspaceId: string) {
    if (workspaceId !== active?.id) switchWorkspace.mutate(workspaceId);
    setOpen(false);
  }

  const triggerLabel = active?.name ?? "Workspaces";

  return (
    <div className="relative" ref={containerRef} data-testid="workspace-switcher">
      <button
        type="button"
        data-testid="workspace-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-1.5 rounded-md border bg-background px-2.5 py-1.5 text-sm text-foreground transition-colors hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
      >
        <Building2 className="size-4 shrink-0 text-muted-foreground" aria-hidden />
        <span className="sr-only">Current workspace: </span>
        <span className="max-w-[10rem] truncate">{triggerLabel}</span>
        <ChevronDown
          className={`size-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Workspaces"
          className="absolute right-0 z-50 mt-2 w-72 rounded-lg border bg-card p-2 shadow-lg"
        >
          {/* What is a workspace? — context first-time users were missing. */}
          <div className="px-2 pb-2 pt-1">
            <p className="text-sm font-semibold text-foreground">Workspaces</p>
            <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
              A workspace is a separate space for one team or client — its own
              signals, saved searches, ICP, and pipeline. Switch between them or
              create a new one.
            </p>
          </div>

          <div className="my-1 border-t" />

          {/* Workspace list / empty state */}
          {isLoading ? (
            <p className="px-2 py-2 text-sm text-muted-foreground" aria-live="polite">
              Loading…
            </p>
          ) : workspaces.length > 0 ? (
            <ul className="max-h-60 overflow-auto py-1">
              {workspaces.map((workspace) => {
                const isActive = workspace.id === active?.id;
                return (
                  <li key={workspace.id}>
                    <button
                      type="button"
                      role="menuitemradio"
                      aria-checked={isActive}
                      disabled={switchWorkspace.isPending}
                      onClick={() => onSelect(workspace.id)}
                      className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-60"
                    >
                      <span className="truncate">{workspace.name}</span>
                      {isActive && (
                        <Check className="size-4 shrink-0 text-primary" aria-hidden />
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="px-2 py-2 text-sm text-muted-foreground">
              No workspace yet. Create one below to get started.
            </p>
          )}

          <div className="my-1 border-t" />

          {/* Create New — always available so the empty state is never a dead end. */}
          <form onSubmit={onCreate} className="flex flex-col gap-2 p-1">
            <label className="sr-only" htmlFor="new-workspace-name">
              New workspace name
            </label>
            <input
              id="new-workspace-name"
              value={name}
              placeholder="New workspace name"
              onChange={(event) => setName(event.target.value)}
              className="rounded-md border bg-background px-2 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            />
            <button
              type="submit"
              disabled={createWorkspace.isPending || name.trim() === ""}
              className="rounded-md bg-primary px-2 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
            >
              {createWorkspace.isPending ? "Creating…" : "Create New"}
            </button>
            {createError ? (
              <span role="alert" className="text-xs text-destructive">
                {createError}
              </span>
            ) : null}
          </form>
        </div>
      )}
    </div>
  );
}
