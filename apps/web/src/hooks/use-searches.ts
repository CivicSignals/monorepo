// Saved-search hooks (H1) — TanStack Query owns the server interactions (doc 06 §2).
//
// Saved searches are workspace-scoped; query keys include workspaceId so cached
// data never bleeds across workspace switches (doc 08 §1.4, B5).
//
// useSavedSearches    — list searches the caller can see (own + shared)
// useCreateSavedSearch — mutation: POST /searches
// useUpdateSavedSearch — mutation: PATCH /searches/{id}
// useDeleteSavedSearch — mutation: DELETE /searches/{id}

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type SavedSearchCreate,
  type SavedSearchOut,
  type SavedSearchPage,
  type SavedSearchUpdate,
  SAVED_SEARCH_PAGE_LIMIT,
  createSavedSearch,
  deleteSavedSearch,
  listSavedSearches,
  updateSavedSearch,
} from "@/lib/searches-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

// ---- Auth/workspace helpers ----

function useAuth() {
  const token = useSessionStore((s) => s.accessToken);
  const workspaceId = useUiStore((s) => s.activeWorkspaceId);
  return { token, workspaceId };
}

// ---- Query keys (workspace-scoped to prevent cross-tenant cache bleed) ----

export const savedSearchKeys = {
  all: ["saved-searches"] as const,
  workspace: (workspaceId: string | null) =>
    [...savedSearchKeys.all, workspaceId ?? "none"] as const,
  list: (workspaceId: string | null) =>
    [...savedSearchKeys.workspace(workspaceId), "list"] as const,
};

// ---- useSavedSearches: list searches in the workspace ----

/** List the saved searches the caller can see in the active workspace (first page). */
export function useSavedSearches() {
  const { token, workspaceId } = useAuth();
  return useQuery<SavedSearchPage>({
    queryKey: savedSearchKeys.list(workspaceId),
    queryFn: () => {
      if (!token || !workspaceId)
        return Promise.resolve({ items: [], next_cursor: null });
      return listSavedSearches(token, workspaceId, {
        limit: SAVED_SEARCH_PAGE_LIMIT,
      });
    },
    enabled: token !== null && workspaceId !== null,
  });
}

// ---- useCreateSavedSearch ----

/** Create a new saved search. Invalidates the workspace list on success. */
export function useCreateSavedSearch() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<SavedSearchOut, Error, SavedSearchCreate>({
    mutationFn: (input) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return createSavedSearch(token, workspaceId, input);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: savedSearchKeys.list(workspaceId),
      });
    },
  });
}

// ---- useUpdateSavedSearch ----

/** Patch a saved search (rename / re-filter / toggle sharing). Refreshes the list. */
export function useUpdateSavedSearch() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<
    SavedSearchOut,
    Error,
    { id: string; update: SavedSearchUpdate }
  >({
    mutationFn: ({ id, update }) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return updateSavedSearch(token, workspaceId, id, update);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: savedSearchKeys.list(workspaceId),
      });
    },
  });
}

// ---- useDeleteSavedSearch ----

/** Delete a saved search. Invalidates the workspace list on success. */
export function useDeleteSavedSearch() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: (id) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return deleteSavedSearch(token, workspaceId, id);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: savedSearchKeys.list(workspaceId),
      });
    },
  });
}
