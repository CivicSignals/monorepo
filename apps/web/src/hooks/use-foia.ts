// FOIA hooks (M4) — TanStack Query owns the server interactions (doc 06 §2).
//
// FOIA requests are workspace-scoped; query keys include workspaceId so that
// cached data never bleeds across workspace switches (doc 08 §1.4, B5).
//
// useFoiaRequests       — infinite-scroll list with status filter
// useFoiaRequest        — single request by id
// useFoiaEvents         — status-transition history for a request
// useFoiaTemplates      — global template library (no workspace scope)
// useCreateFoiaRequest  — mutation: create (draft)
// useUpdateFoiaRequest  — mutation: patch draft fields
// useTransitionFoiaRequest — mutation: advance state machine

"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from "@tanstack/react-query";
import {
  type FoiaRequestFilters,
  type FoiaRequestPage,
  type FoiaRequestRead,
  type FoiaRequestEventRead,
  type FoiaRequestCreate,
  type FoiaRequestUpdate,
  type FoiaRequestTransition,
  type FoiaTemplateList,
  type FoiaAttachmentRead,
  type FoiaAttachmentPage,
  type FoiaAttachmentSignalRef,
  FOIA_PAGE_LIMIT,
  listFoiaRequests,
  getFoiaRequest,
  createFoiaRequest,
  updateFoiaRequest,
  transitionFoiaRequest,
  listFoiaRequestEvents,
  listFoiaTemplates,
  uploadFoiaAttachment,
  listFoiaAttachments,
  listAttachmentSignals,
} from "@/lib/foia-api";
import { useSessionStore } from "@/store/session";
import { useUiStore } from "@/store/ui";

// ---- Auth/workspace helpers ----

function useAuth() {
  const token = useSessionStore((s) => s.accessToken);
  const workspaceId = useUiStore((s) => s.activeWorkspaceId);
  return { token, workspaceId };
}

// ---- Query keys ----

export const foiaKeys = {
  all: ["foia"] as const,
  requests: (workspaceId: string | null) =>
    [...foiaKeys.all, "requests", workspaceId ?? "none"] as const,
  requestsList: (workspaceId: string | null, filters: Omit<FoiaRequestFilters, "cursor" | "limit">) =>
    [...foiaKeys.requests(workspaceId), "list", filters] as const,
  requestDetail: (workspaceId: string | null, id: string) =>
    [...foiaKeys.requests(workspaceId), "detail", id] as const,
  requestEvents: (workspaceId: string | null, requestId: string) =>
    [...foiaKeys.requestDetail(workspaceId, requestId), "events"] as const,
  templates: () => [...foiaKeys.all, "templates"] as const,
  // M3: attachments
  attachments: (workspaceId: string | null, requestId: string) =>
    [...foiaKeys.requestDetail(workspaceId, requestId), "attachments"] as const,
  attachmentSignals: (workspaceId: string | null, requestId: string, attachmentId: string) =>
    [...foiaKeys.attachments(workspaceId, requestId), attachmentId, "signals"] as const,
};

// ---- useFoiaRequests: infinite scroll list ----

/**
 * Infinite-scroll hook for the FOIA request list, scoped to the active workspace.
 * Filters (status, entity_id) are applied server-side.
 */
export function useFoiaRequests(
  filters: Omit<FoiaRequestFilters, "cursor" | "limit"> = {},
) {
  const { token, workspaceId } = useAuth();
  return useInfiniteQuery<
    FoiaRequestPage,
    Error,
    InfiniteData<FoiaRequestPage>,
    ReturnType<typeof foiaKeys.requestsList>,
    string | undefined
  >({
    queryKey: foiaKeys.requestsList(workspaceId, filters),
    queryFn: ({ pageParam }) => {
      if (!token || !workspaceId) return Promise.resolve({ items: [], next_cursor: null });
      return listFoiaRequests(token, workspaceId, {
        ...filters,
        cursor: pageParam,
        limit: FOIA_PAGE_LIMIT,
      });
    },
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: token !== null && workspaceId !== null,
  });
}

// ---- useFoiaRequest: single request by id ----

/** Fetch a single FOIA request by id. Returns null when 404. */
export function useFoiaRequest(id: string | undefined) {
  const { token, workspaceId } = useAuth();
  return useQuery<FoiaRequestRead | null>({
    queryKey: foiaKeys.requestDetail(workspaceId, id ?? ""),
    queryFn: () => {
      if (!token || !workspaceId || !id) return Promise.resolve(null);
      return getFoiaRequest(token, workspaceId, id);
    },
    enabled: token !== null && workspaceId !== null && id !== undefined && id !== "",
  });
}

// ---- useFoiaEvents: status-transition history ----

/** Fetch the transition event log for a FOIA request. */
export function useFoiaEvents(requestId: string | undefined) {
  const { token, workspaceId } = useAuth();
  return useQuery<FoiaRequestEventRead[]>({
    queryKey: foiaKeys.requestEvents(workspaceId, requestId ?? ""),
    queryFn: () => {
      if (!token || !workspaceId || !requestId) return Promise.resolve([]);
      return listFoiaRequestEvents(token, workspaceId, requestId);
    },
    enabled:
      token !== null && workspaceId !== null && requestId !== undefined && requestId !== "",
  });
}

// ---- useFoiaTemplates: global template library ----

/** Fetch all available FOIA templates (global, no workspace scope). */
export function useFoiaTemplates() {
  return useQuery<FoiaTemplateList>({
    queryKey: foiaKeys.templates(),
    queryFn: listFoiaTemplates,
    staleTime: 10 * 60 * 1000, // Templates change rarely; cache for 10 min.
  });
}

// ---- useCreateFoiaRequest ----

/** Create a new FOIA request (always starts as draft). */
export function useCreateFoiaRequest() {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<FoiaRequestRead, Error, FoiaRequestCreate>({
    mutationFn: (input) => {
      if (!token || !workspaceId) return Promise.reject(new Error("not authenticated"));
      return createFoiaRequest(token, workspaceId, input);
    },
    onSuccess: () => {
      // Invalidate the list so the new request appears.
      void queryClient.invalidateQueries({
        queryKey: foiaKeys.requests(workspaceId),
      });
    },
  });
}

// ---- useUpdateFoiaRequest ----

/** Patch a draft FOIA request (subject, body, submission details). */
export function useUpdateFoiaRequest(requestId: string) {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<FoiaRequestRead, Error, FoiaRequestUpdate>({
    mutationFn: (update) => {
      if (!token || !workspaceId) return Promise.reject(new Error("not authenticated"));
      return updateFoiaRequest(token, workspaceId, requestId, update);
    },
    onSuccess: (updated) => {
      // Update the cached detail and invalidate the list.
      queryClient.setQueryData(foiaKeys.requestDetail(workspaceId, requestId), updated);
      void queryClient.invalidateQueries({
        queryKey: foiaKeys.requests(workspaceId),
      });
    },
  });
}

// ---- useTransitionFoiaRequest ----

/** Advance the FOIA request state machine: draft→sent→ack→response. */
export function useTransitionFoiaRequest(requestId: string) {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<FoiaRequestRead, Error, FoiaRequestTransition>({
    mutationFn: (transition) => {
      if (!token || !workspaceId) return Promise.reject(new Error("not authenticated"));
      return transitionFoiaRequest(token, workspaceId, requestId, transition);
    },
    onSuccess: (updated) => {
      // Update the cached detail and invalidate the list and event log.
      queryClient.setQueryData(foiaKeys.requestDetail(workspaceId, requestId), updated);
      void queryClient.invalidateQueries({
        queryKey: foiaKeys.requests(workspaceId),
      });
      void queryClient.invalidateQueries({
        queryKey: foiaKeys.requestEvents(workspaceId, requestId),
      });
    },
  });
}

// ---- M3: Attachment hooks ----

/** Fetch all attachments for a FOIA request (non-paginated for UI simplicity). */
export function useFoiaAttachments(requestId: string | undefined) {
  const { token, workspaceId } = useAuth();
  return useQuery<FoiaAttachmentPage>({
    queryKey: foiaKeys.attachments(workspaceId, requestId ?? ""),
    queryFn: () => {
      if (!token || !workspaceId || !requestId) return Promise.resolve({ items: [], next_cursor: null });
      return listFoiaAttachments(token, workspaceId, requestId);
    },
    enabled: token !== null && workspaceId !== null && requestId !== undefined && requestId !== "",
  });
}

/** Upload a response PDF and enqueue extraction; invalidates the attachment list. */
export function useUploadFoiaAttachment(requestId: string) {
  const { token, workspaceId } = useAuth();
  const queryClient = useQueryClient();

  return useMutation<FoiaAttachmentRead, Error, File>({
    mutationFn: (file) => {
      if (!token || !workspaceId) return Promise.reject(new Error("not authenticated"));
      return uploadFoiaAttachment(token, workspaceId, requestId, file);
    },
    onSuccess: () => {
      // Invalidate the attachments list and the parent request (status may have changed).
      void queryClient.invalidateQueries({
        queryKey: foiaKeys.attachments(workspaceId, requestId),
      });
      void queryClient.invalidateQueries({
        queryKey: foiaKeys.requestDetail(workspaceId, requestId),
      });
    },
  });
}

/** Fetch signals linked to a FOIA attachment. */
export function useFoiaAttachmentSignals(
  requestId: string | undefined,
  attachmentId: string | undefined,
) {
  const { token, workspaceId } = useAuth();
  return useQuery<FoiaAttachmentSignalRef[]>({
    queryKey: foiaKeys.attachmentSignals(workspaceId, requestId ?? "", attachmentId ?? ""),
    queryFn: () => {
      if (!token || !workspaceId || !requestId || !attachmentId) return Promise.resolve([]);
      return listAttachmentSignals(token, workspaceId, requestId, attachmentId);
    },
    enabled:
      token !== null &&
      workspaceId !== null &&
      requestId !== undefined &&
      requestId !== "" &&
      attachmentId !== undefined &&
      attachmentId !== "",
  });
}
