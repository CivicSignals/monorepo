// Salesforce integration API client for the web app (K2).
//
// Talks to the FastAPI integrations endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1). TanStack Query owns the *server* state
// (see src/hooks/use-salesforce.ts); this module is the thin transport.
//
// Surface (doc 08 §3.6, K2): list/connect/disconnect connections, discover the
// provider's objects + fields, save a per-connection field mapping, and push a
// signal/pipeline-item. All endpoints are admin-gated + workspace-scoped by the
// API; a non-admin / wrong-workspace caller gets a ProblemError surfaced inline.

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type ConnectionStatus =
  | "pending_oauth"
  | "healthy"
  | "degraded"
  | "needs_reauth"
  | "revoked";

export interface Connection {
  id: string;
  provider: string;
  name: string;
  status: ConnectionStatus;
  scopes: string[];
  default_targets: string[];
  provider_account: Record<string, unknown>;
  connected_at: string | null;
  last_push_at: string | null;
  created_at: string;
}

export interface ConnectionCreated {
  id: string;
  provider: string;
  status: ConnectionStatus;
  redirect_url: string | null;
}

export interface DiscoveredObject {
  name: string;
  label: string;
  custom: boolean;
}

export interface DiscoveredField {
  name: string;
  label: string;
  type: string;
  required: boolean;
  createable: boolean;
  updateable: boolean;
}

export interface FieldMapping {
  id: string;
  connection_id: string;
  target_object: string;
  field_map: Record<string, string>;
  constants: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface PushLogEntry {
  id: string;
  connection_id: string;
  signal_id: string | null;
  pipeline_item_id: string | null;
  target: string;
  status: "pending" | "success" | "failed" | "dead_letter";
  external_id: string | null;
  error: { code: string; message: string | null } | null;
  attempt_count: number;
  retry_at: string | null;
  attempted_at: string | null;
  created_at: string;
}

export interface FieldMappingInput {
  target_object: string;
  field_map: Record<string, string>;
  constants?: Record<string, unknown>;
}

export interface PushInput {
  source: Record<string, unknown>;
  target?: string;
  field_map_override?: Record<string, string>;
  signal_id?: string;
  pipeline_item_id?: string;
  idempotency_key?: string;
}

async function request<T>(
  path: string,
  init: RequestInit & { token?: string; workspaceId?: string } = {},
): Promise<T> {
  const { token, workspaceId, headers, ...rest } = init;
  const merged = new Headers(headers);
  merged.set("Accept", "application/json");
  if (rest.body !== undefined) {
    merged.set("Content-Type", "application/json");
  }
  if (token) merged.set("Authorization", `Bearer ${token}`);
  if (workspaceId) merged.set("X-Workspace-Id", workspaceId);

  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: merged,
  });
  if (!res.ok) {
    const problem = (await res.json().catch(() => ({
      type: "about:blank",
      title: res.statusText,
      status: res.status,
    }))) as Problem;
    throw new ProblemError(problem);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ---- Connections ----

export async function listConnections(
  token: string,
  workspaceId: string,
): Promise<Connection[]> {
  const body = await request<{ data: Connection[] }>(
    "/integrations/connections",
    { token, workspaceId },
  );
  return body.data;
}

export function connectSalesforce(
  token: string,
  workspaceId: string,
  name: string,
): Promise<ConnectionCreated> {
  return request<ConnectionCreated>("/integrations/connections", {
    method: "POST",
    body: JSON.stringify({
      provider: "salesforce",
      name,
      default_targets: ["Opportunity"],
    }),
    token,
    workspaceId,
  });
}

export function disconnect(
  token: string,
  workspaceId: string,
  connectionId: string,
): Promise<void> {
  return request<void>(`/integrations/connections/${connectionId}`, {
    method: "DELETE",
    token,
    workspaceId,
  });
}

// ---- Discovery ----

export async function discoverObjects(
  token: string,
  workspaceId: string,
  connectionId: string,
): Promise<DiscoveredObject[]> {
  const body = await request<{ data: DiscoveredObject[] }>(
    `/integrations/connections/${connectionId}/discover/objects`,
    { token, workspaceId },
  );
  return body.data;
}

export async function discoverFields(
  token: string,
  workspaceId: string,
  connectionId: string,
  objectName: string,
): Promise<DiscoveredField[]> {
  const body = await request<{ object: string; data: DiscoveredField[] }>(
    `/integrations/connections/${connectionId}/discover/fields?object=${encodeURIComponent(objectName)}`,
    { token, workspaceId },
  );
  return body.data;
}

// ---- Field mappings ----

export async function listFieldMappings(
  token: string,
  workspaceId: string,
  connectionId: string,
): Promise<FieldMapping[]> {
  const body = await request<{ data: FieldMapping[] }>(
    `/integrations/connections/${connectionId}/field-mappings`,
    { token, workspaceId },
  );
  return body.data;
}

export function saveFieldMapping(
  token: string,
  workspaceId: string,
  connectionId: string,
  input: FieldMappingInput,
): Promise<FieldMapping> {
  return request<FieldMapping>(
    `/integrations/connections/${connectionId}/field-mappings`,
    {
      method: "PUT",
      body: JSON.stringify(input),
      token,
      workspaceId,
    },
  );
}

export function deleteFieldMapping(
  token: string,
  workspaceId: string,
  connectionId: string,
  targetObject: string,
): Promise<void> {
  return request<void>(
    `/integrations/connections/${connectionId}/field-mappings/${encodeURIComponent(targetObject)}`,
    { method: "DELETE", token, workspaceId },
  );
}

// ---- Push ----

export async function pushSignal(
  token: string,
  workspaceId: string,
  connectionId: string,
  input: PushInput,
): Promise<PushLogEntry> {
  const body = await request<{ push_log: PushLogEntry }>(
    `/integrations/connections/${connectionId}/push`,
    {
      method: "POST",
      body: JSON.stringify(input),
      token,
      workspaceId,
    },
  );
  return body.push_log;
}
