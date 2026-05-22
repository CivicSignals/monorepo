// Salesforce integration hooks (K2) — TanStack Query owns server state (doc 06 §2).
//
// Drives the Salesforce connection + field-mapping UI: list/connect/disconnect
// connections, discover the org's objects + fields, save a per-connection field
// mapping, and push a signal/pipeline-item. All endpoints are admin-gated +
// workspace-scoped by the API; the active workspace id flows in from the caller.

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type Connection,
  type ConnectionCreated,
  type DiscoveredField,
  type DiscoveredObject,
  type FieldMapping,
  type FieldMappingInput,
  type PushInput,
  type PushLogEntry,
  connectHubspot,
  connectSalesforce,
  deleteFieldMapping,
  disconnect,
  discoverFields,
  discoverObjects,
  listConnections,
  listFieldMappings,
  pushSignal,
  saveFieldMapping,
} from "@/lib/salesforce-api";
import { useSessionStore } from "@/store/session";

export const connectionsKey = (workspaceId: string | undefined) =>
  ["integrations", "connections", workspaceId ?? "none"] as const;

export const objectsKey = (
  workspaceId: string | undefined,
  connectionId: string | undefined,
) => ["integrations", "objects", workspaceId ?? "none", connectionId ?? "none"] as const;

export const fieldsKey = (
  workspaceId: string | undefined,
  connectionId: string | undefined,
  objectName: string | undefined,
) =>
  [
    "integrations",
    "fields",
    workspaceId ?? "none",
    connectionId ?? "none",
    objectName ?? "none",
  ] as const;

export const fieldMappingsKey = (
  workspaceId: string | undefined,
  connectionId: string | undefined,
) =>
  ["integrations", "field-mappings", workspaceId ?? "none", connectionId ?? "none"] as const;

/** List the active workspace's integration connections (admin only). */
export function useConnections(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<Connection[]>({
    queryKey: connectionsKey(workspaceId),
    queryFn: async () => {
      if (!token || !workspaceId) return [];
      return listConnections(token, workspaceId);
    },
    enabled: token !== null && !!workspaceId,
  });
}

/** Start the Salesforce OAuth flow; resolves with a redirect_url to consent. */
export function useConnectSalesforce(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<ConnectionCreated, Error, string>({
    mutationFn: (name) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("no active workspace"));
      return connectSalesforce(token, workspaceId, name);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: connectionsKey(workspaceId),
      });
    },
  });
}

/** Start the HubSpot OAuth flow (K3); resolves with a redirect_url to consent. */
export function useConnectHubspot(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<ConnectionCreated, Error, string>({
    mutationFn: (name) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("no active workspace"));
      return connectHubspot(token, workspaceId, name);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: connectionsKey(workspaceId),
      });
    },
  });
}

/** Disconnect a connection and refresh the list. */
export function useDisconnect(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (connectionId) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("no active workspace"));
      return disconnect(token, workspaceId, connectionId);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: connectionsKey(workspaceId),
      });
    },
  });
}

/** Discover the connection's pushable provider objects (object dropdown). */
export function useObjects(
  workspaceId: string | undefined,
  connectionId: string | undefined,
  enabled = true,
) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<DiscoveredObject[]>({
    queryKey: objectsKey(workspaceId, connectionId),
    queryFn: async () => {
      if (!token || !workspaceId || !connectionId) return [];
      return discoverObjects(token, workspaceId, connectionId);
    },
    enabled: token !== null && !!workspaceId && !!connectionId && enabled,
  });
}

/** Discover the writable fields on an object (field-mapping rows). */
export function useFields(
  workspaceId: string | undefined,
  connectionId: string | undefined,
  objectName: string | undefined,
) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<DiscoveredField[]>({
    queryKey: fieldsKey(workspaceId, connectionId, objectName),
    queryFn: async () => {
      if (!token || !workspaceId || !connectionId || !objectName) return [];
      return discoverFields(token, workspaceId, connectionId, objectName);
    },
    enabled:
      token !== null && !!workspaceId && !!connectionId && !!objectName,
  });
}

/** List the connection's saved field mappings. */
export function useFieldMappings(
  workspaceId: string | undefined,
  connectionId: string | undefined,
) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<FieldMapping[]>({
    queryKey: fieldMappingsKey(workspaceId, connectionId),
    queryFn: async () => {
      if (!token || !workspaceId || !connectionId) return [];
      return listFieldMappings(token, workspaceId, connectionId);
    },
    enabled: token !== null && !!workspaceId && !!connectionId,
  });
}

/** Save (upsert) a field mapping for one target object. */
export function useSaveFieldMapping(
  workspaceId: string | undefined,
  connectionId: string | undefined,
) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<FieldMapping, Error, FieldMappingInput>({
    mutationFn: (input) => {
      if (!token || !workspaceId || !connectionId)
        return Promise.reject(new Error("no active workspace"));
      return saveFieldMapping(token, workspaceId, connectionId, input);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: fieldMappingsKey(workspaceId, connectionId),
      });
    },
  });
}

/** Delete a field mapping for a target object. */
export function useDeleteFieldMapping(
  workspaceId: string | undefined,
  connectionId: string | undefined,
) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (targetObject) => {
      if (!token || !workspaceId || !connectionId)
        return Promise.reject(new Error("no active workspace"));
      return deleteFieldMapping(token, workspaceId, connectionId, targetObject);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: fieldMappingsKey(workspaceId, connectionId),
      });
    },
  });
}

/** Push a signal/pipeline-item to the connection's provider; resolves the log. */
export function usePushSignal(
  workspaceId: string | undefined,
  connectionId: string | undefined,
) {
  const token = useSessionStore((s) => s.accessToken);
  return useMutation<PushLogEntry, Error, PushInput>({
    mutationFn: (input) => {
      if (!token || !workspaceId || !connectionId)
        return Promise.reject(new Error("no active workspace"));
      return pushSignal(token, workspaceId, connectionId, input);
    },
  });
}
