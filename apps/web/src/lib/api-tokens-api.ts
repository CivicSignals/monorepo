// API-tokens API client for the web app (B8).
//
// Talks to the FastAPI token endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1). TanStack Query owns the *server* state
// (see src/hooks/use-api-tokens.ts); this module is the thin transport.
//
// Two token kinds (doc 08 §1.3):
//   - personal access tokens (PATs): /auth/tokens — act as the owning user;
//   - workspace tokens: /workspaces/{id}/api-tokens — admin-gated, one tenant.
// The plaintext secret is returned ONLY on create ("revealed once") and never
// again (threat-model §4.2); list responses carry metadata only.

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type ApiTokenType = "workspace" | "personal";

export interface ApiToken {
  id: string;
  token_type: ApiTokenType;
  name: string;
  token_prefix: string;
  scopes: string[];
  workspace_id: string | null;
  user_id: string;
  created_by_user_id: string | null;
  last_used_at: string | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
}

// Create responses additionally carry the one-time plaintext secret.
export interface ApiTokenCreated extends ApiToken {
  token: string;
}

export interface ApiTokenList {
  items: ApiToken[];
}

export interface ApiTokenScopes {
  scopes: string[];
}

export interface CreateTokenInput {
  name: string;
  scopes: string[];
  expires_at?: string | null;
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

// ---- Scopes catalog ----

export function listTokenScopes(token: string): Promise<ApiTokenScopes> {
  return request<ApiTokenScopes>("/auth/tokens/scopes", { token });
}

// ---- Personal access tokens (PATs) ----

export function listPersonalTokens(token: string): Promise<ApiTokenList> {
  return request<ApiTokenList>("/auth/tokens", { token });
}

export function createPersonalToken(
  token: string,
  input: CreateTokenInput,
): Promise<ApiTokenCreated> {
  return request<ApiTokenCreated>("/auth/tokens", {
    method: "POST",
    body: JSON.stringify(input),
    token,
  });
}

export function revokePersonalToken(
  token: string,
  tokenId: string,
): Promise<void> {
  return request<void>(`/auth/tokens/${tokenId}`, { method: "DELETE", token });
}

// ---- Workspace API tokens (admin only) ----

export function listWorkspaceTokens(
  token: string,
  workspaceId: string,
): Promise<ApiTokenList> {
  return request<ApiTokenList>(`/workspaces/${workspaceId}/api-tokens`, {
    token,
    workspaceId,
  });
}

export function createWorkspaceToken(
  token: string,
  workspaceId: string,
  input: CreateTokenInput,
): Promise<ApiTokenCreated> {
  return request<ApiTokenCreated>(`/workspaces/${workspaceId}/api-tokens`, {
    method: "POST",
    body: JSON.stringify(input),
    token,
    workspaceId,
  });
}

export function revokeWorkspaceToken(
  token: string,
  workspaceId: string,
  tokenId: string,
): Promise<void> {
  return request<void>(`/workspaces/${workspaceId}/api-tokens/${tokenId}`, {
    method: "DELETE",
    token,
    workspaceId,
  });
}
