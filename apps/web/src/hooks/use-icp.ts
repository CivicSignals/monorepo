// ICP hooks (F2) — TanStack Query owns the server interactions (doc 06 §2).
//
// ICPs are workspace-scoped; query keys include workspaceId so cached data
// never bleeds across workspace switches (doc 08 §1.4, B5).
//
// useIcps          — list all ICPs in the active workspace
// useActiveIcp     — the single is_active ICP (first in list, or null)
// useIcp           — single ICP by id
// useCreateIcp     — mutation: POST /icp
// useUpdateIcp     — mutation: PATCH /icp/{id}
// useActivateIcp   — mutation: POST /icp/{id}/activate
// useDeactivateIcp — mutation: POST /icp/{id}/deactivate

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type IcpCreate,
  type IcpOut,
  type IcpPage,
  type IcpUpdate,
  ICP_PAGE_LIMIT,
  listIcps,
  getIcp,
  createIcp,
  updateIcp,
  activateIcp,
  deactivateIcp,
} from "@/lib/icp-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

// ---- Auth/workspace helpers ----

function useAuth() {
  const token = useSessionStore((s) => s.accessToken);
  const workspaceId = useUiStore((s) => s.activeWorkspaceId);
  return { token, workspaceId };
}

// ---- Query keys (workspace-scoped to prevent cross-tenant cache bleed) ----

export const icpKeys = {
  all: ["icp"] as const,
  workspace: (workspaceId: string | null) =>
    [...icpKeys.all, workspaceId ?? "none"] as const,
  list: (workspaceId: string | null) =>
    [...icpKeys.workspace(workspaceId), "list"] as const,
  detail: (workspaceId: string | null, id: string) =>
    [...icpKeys.workspace(workspaceId), "detail", id] as const,
};

// ---- useIcps: list all ICPs in the workspace ----

/** List all ICP definitions in the active workspace (first page; ICPs are few). */
export function useIcps() {
  const { token, workspaceId } = useAuth();
  return useQuery<IcpPage>({
    queryKey: icpKeys.list(workspaceId),
    queryFn: () => {
      if (!token || !workspaceId)
        return Promise.resolve({ items: [], next_cursor: null });
      return listIcps(token, workspaceId, { limit: ICP_PAGE_LIMIT });
    },
    enabled: token !== null && workspaceId !== null,
  });
}

// ---- useActiveIcp: the single active ICP for the workspace ----

/**
 * Returns the first active ICP in the workspace, or null.
 * Most workspaces have exactly one; the wizard drives creation.
 */
export function useActiveIcp(): IcpOut | null | undefined {
  const { data } = useIcps();
  if (!data) return undefined; // loading
  return data.items.find((icp) => icp.is_active) ?? null;
}

// ---- useIcp: single ICP by id ----

/** Fetch a single ICP by id. Returns null when 404. */
export function useIcp(id: string | undefined) {
  const { token, workspaceId } = useAuth();
  return useQuery<IcpOut | null>({
    queryKey: icpKeys.detail(workspaceId, id ?? ""),
    queryFn: () => {
      if (!token || !workspaceId || !id) return Promise.resolve(null);
      return getIcp(token, workspaceId, id);
    },
    enabled: token !== null && workspaceId !== null && id !== undefined && id !== "",
  });
}

// ---- useCreateIcp ----

/** Create a new ICP definition. Invalidates the workspace list on success. */
export function useCreateIcp() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<IcpOut, Error, IcpCreate>({
    mutationFn: (input) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return createIcp(token, workspaceId, input);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: icpKeys.list(workspaceId),
      });
    },
  });
}

// ---- useUpdateIcp ----

/** Patch an existing ICP. Refreshes the detail + list caches on success. */
export function useUpdateIcp(icpId: string) {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<IcpOut, Error, IcpUpdate>({
    mutationFn: (update) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return updateIcp(token, workspaceId, icpId, update);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(icpKeys.detail(workspaceId, icpId), updated);
      void queryClient.invalidateQueries({
        queryKey: icpKeys.list(workspaceId),
      });
    },
  });
}

// ---- useActivateIcp ----

/** Activate an ICP, deactivating all others in the workspace. */
export function useActivateIcp() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<IcpOut, Error, string>({
    mutationFn: (id) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return activateIcp(token, workspaceId, id);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(
        icpKeys.detail(workspaceId, updated.id),
        updated,
      );
      void queryClient.invalidateQueries({
        queryKey: icpKeys.list(workspaceId),
      });
    },
  });
}

// ---- useDeactivateIcp ----

/** Deactivate an ICP (workspace will have no active ICP). */
export function useDeactivateIcp() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<IcpOut, Error, string>({
    mutationFn: (id) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("not authenticated"));
      return deactivateIcp(token, workspaceId, id);
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(
        icpKeys.detail(workspaceId, updated.id),
        updated,
      );
      void queryClient.invalidateQueries({
        queryKey: icpKeys.list(workspaceId),
      });
    },
  });
}
