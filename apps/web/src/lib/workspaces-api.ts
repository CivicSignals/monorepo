// Workspaces API client for the web app (B5).
//
// Talks to the FastAPI workspace endpoints under NEXT_PUBLIC_API_BASE_URL
// (e.g. http://localhost:8000/api/v1). TanStack Query owns the *server* state
// (see src/hooks/use-workspaces.ts); this module is the thin transport.
//
// Workspace-scoped requests send the `X-Workspace-Id` header (doc 08 §1.4) so
// the API resolves the active tenant. The workspace endpoints themselves are
// scoped by the caller's membership, so they don't strictly need the header,
// but every *other* scoped call in the app should — `request` accepts an
// optional `workspaceId` to make that the default ergonomic path.

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type MembershipRole = "owner" | "admin" | "member" | "viewer";

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  organization_id: string;
  owner_id: string;
  country_default: string;
  role: MembershipRole | null;
  created_at: string;
  updated_at: string;
}

export interface WorkspacePage {
  items: Workspace[];
  next_cursor: string | null;
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
  // The workspace-scoping seam (doc 08 §1.4): every scoped call carries the
  // active workspace id. When absent the API falls back to last_active.
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

export function listWorkspaces(token: string): Promise<WorkspacePage> {
  return request<WorkspacePage>("/workspaces", { token });
}

export interface CreateWorkspaceInput {
  name: string;
  slug?: string;
  country_default?: string;
}

export function createWorkspace(
  token: string,
  input: CreateWorkspaceInput,
): Promise<Workspace> {
  return request<Workspace>("/workspaces", {
    method: "POST",
    body: JSON.stringify(input),
    token,
  });
}

export function switchWorkspace(
  token: string,
  workspaceId: string,
): Promise<Workspace> {
  return request<Workspace>(`/workspaces/${workspaceId}/switch`, {
    method: "POST",
    body: JSON.stringify({}),
    token,
  });
}
