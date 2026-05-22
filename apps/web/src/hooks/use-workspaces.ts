// Workspace hooks (B5) — TanStack Query owns the server interactions (doc 06 §2).
//
// Query: the workspaces the current user belongs to. Mutations: create + switch.
// The *selected* workspace id is client UI state (Zustand, src/store/ui.ts); the
// workspace records are server state and live here. On login we default the
// selection to the user's most recent workspace (the switch endpoint records
// last_active server-side, doc 08 §1.4).

"use client";

import { useEffect, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type CreateWorkspaceInput,
  type Workspace,
  type WorkspacePage,
  createWorkspace as createWorkspaceApi,
  listWorkspaces as listWorkspacesApi,
  switchWorkspace as switchWorkspaceApi,
} from "@/lib/workspaces-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

export const WORKSPACES_QUERY_KEY = ["workspaces"] as const;

export function useWorkspaces() {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<WorkspacePage>({
    queryKey: WORKSPACES_QUERY_KEY,
    queryFn: () =>
      token
        ? listWorkspacesApi(token)
        : Promise.resolve({ items: [], next_cursor: null }),
    enabled: token !== null,
  });
}

/**
 * Returns the currently-active workspace (UI selection joined to server data),
 * and keeps a sensible default selected: when nothing is selected yet (fresh
 * login / refresh), it picks the first workspace the user belongs to.
 */
export function useActiveWorkspace(): Workspace | null {
  const { data } = useWorkspaces();
  const activeId = useUiStore((s) => s.activeWorkspaceId);
  const setActiveId = useUiStore((s) => s.setActiveWorkspaceId);
  // Memoize so the array identity is stable across renders (the useEffect below
  // depends on it; an inline `?? []` would re-run the effect every render).
  const items = useMemo(() => data?.items ?? [], [data]);

  useEffect(() => {
    if (items.length === 0) return;
    const stillValid = activeId && items.some((w) => w.id === activeId);
    if (!stillValid) setActiveId(items[0].id);
  }, [activeId, items, setActiveId]);

  return items.find((w) => w.id === activeId) ?? items[0] ?? null;
}

export function useCreateWorkspace() {
  const token = useSessionStore((s) => s.accessToken);
  const setActiveId = useUiStore((s) => s.setActiveWorkspaceId);
  const queryClient = useQueryClient();

  return useMutation<Workspace, Error, CreateWorkspaceInput>({
    mutationFn: (input) => {
      if (!token) return Promise.reject(new Error("not authenticated"));
      return createWorkspaceApi(token, input);
    },
    onSuccess: (workspace) => {
      // Newly-created workspace becomes the active one.
      setActiveId(workspace.id);
      void queryClient.invalidateQueries({ queryKey: WORKSPACES_QUERY_KEY });
    },
  });
}

export function useSwitchWorkspace() {
  const token = useSessionStore((s) => s.accessToken);
  const setActiveId = useUiStore((s) => s.setActiveWorkspaceId);

  return useMutation<Workspace, Error, string>({
    mutationFn: (workspaceId) => {
      if (!token) return Promise.reject(new Error("not authenticated"));
      return switchWorkspaceApi(token, workspaceId);
    },
    onSuccess: (workspace) => {
      // Persist the selection client-side; the server already recorded
      // last_active so a subsequent header-less request scopes here too.
      setActiveId(workspace.id);
    },
  });
}
