"use client";

import { useState } from "react";
import { ProblemError } from "@/lib/auth-api";
import {
  useActiveWorkspace,
  useCreateWorkspace,
  useSwitchWorkspace,
  useWorkspaces,
} from "@/hooks/use-workspaces";

// Header workspace switcher + inline create flow (B5).
//
// - A <select> lists the workspaces the user belongs to and switches the active
//   one (POST /workspaces/{id}/switch sets last_active server-side).
// - A small inline form creates a new workspace and makes it active.
// Server state is TanStack Query (useWorkspaces); the active selection is the
// Zustand UI slice surfaced via useActiveWorkspace.
export function WorkspaceSwitcher() {
  const { data, isLoading } = useWorkspaces();
  const active = useActiveWorkspace();
  const switchWorkspace = useSwitchWorkspace();
  const createWorkspace = useCreateWorkspace();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  const workspaces = data ?? [];

  if (isLoading) {
    return (
      <span className="text-sm text-muted-foreground" aria-live="polite">
        Loading workspaces…
      </span>
    );
  }

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
          setCreating(false);
        },
      },
    );
  }

  return (
    <div className="flex items-center gap-2" data-testid="workspace-switcher">
      {workspaces.length > 0 ? (
        <label className="flex items-center gap-2 text-sm">
          <span className="sr-only">Active workspace</span>
          <select
            aria-label="Active workspace"
            value={active?.id ?? ""}
            disabled={switchWorkspace.isPending}
            onChange={(event) => switchWorkspace.mutate(event.target.value)}
            className="rounded-md border bg-background px-2 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-60"
          >
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <span className="text-sm text-muted-foreground">No workspaces yet</span>
      )}

      {creating ? (
        <form onSubmit={onCreate} className="flex items-center gap-2">
          <label className="sr-only" htmlFor="new-workspace-name">
            New workspace name
          </label>
          <input
            id="new-workspace-name"
            value={name}
            autoFocus
            placeholder="Workspace name"
            onChange={(event) => setName(event.target.value)}
            className="rounded-md border bg-background px-2 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          />
          <button
            type="submit"
            disabled={createWorkspace.isPending || name.trim() === ""}
            className="rounded-md bg-primary px-2 py-1.5 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-60"
          >
            {createWorkspace.isPending ? "Creating…" : "Create"}
          </button>
          <button
            type="button"
            onClick={() => {
              setCreating(false);
              setName("");
            }}
            className="rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            Cancel
          </button>
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          New workspace
        </button>
      )}

      {createError ? (
        <span role="alert" className="text-sm text-destructive">
          {createError}
        </span>
      ) : null}
    </div>
  );
}
