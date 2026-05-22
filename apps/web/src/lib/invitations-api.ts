// Invitations API client for the web app (B6).
//
// Talks to the FastAPI invitation endpoints under NEXT_PUBLIC_API_BASE_URL.
// TanStack Query owns the server state (see src/hooks/use-invitations.ts);
// this module is the thin transport layer.
//
// Admin endpoints (create / list / revoke / resend) are workspace-scoped and
// require the `admin` role. The accept endpoint is authenticated but does not
// require workspace membership — the invitee is joining via the link.

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type MembershipRole = "owner" | "admin" | "member" | "viewer";
export type InvitationStatus = "pending" | "accepted" | "revoked" | "expired";

export interface Invitation {
  id: string;
  workspace_id: string;
  invited_email: string;
  role: MembershipRole;
  status: InvitationStatus;
  invited_by: string | null;
  accepted_at: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
}

export interface InvitationPage {
  items: Invitation[];
  next_cursor: string | null;
}

export interface CreateInvitationInput {
  invited_email: string;
  role: MembershipRole;
}

export interface AcceptInvitationInput {
  token: string;
}

// Member record returned on accept.
export interface MemberOut {
  id: string;
  workspace_id: string;
  user_id: string;
  role: MembershipRole;
  invited_by: string | null;
  invited_at: string | null;
  joined_at: string | null;
  created_at: string;
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

// ---- Admin invitation endpoints (workspace-scoped, admin-gated) ----

export function createInvitation(
  token: string,
  workspaceId: string,
  input: CreateInvitationInput,
): Promise<Invitation> {
  return request<Invitation>(`/workspaces/${workspaceId}/invitations`, {
    method: "POST",
    body: JSON.stringify(input),
    token,
    workspaceId,
  });
}

export function listInvitations(
  token: string,
  workspaceId: string,
  opts: { status?: InvitationStatus; cursor?: string; limit?: number } = {},
): Promise<InvitationPage> {
  const params = new URLSearchParams();
  if (opts.status) params.set("status", opts.status);
  if (opts.cursor) params.set("cursor", opts.cursor);
  if (opts.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return request<InvitationPage>(
    `/workspaces/${workspaceId}/invitations${qs ? `?${qs}` : ""}`,
    { token, workspaceId },
  );
}

export function revokeInvitation(
  token: string,
  workspaceId: string,
  invitationId: string,
): Promise<void> {
  return request<void>(
    `/workspaces/${workspaceId}/invitations/${invitationId}`,
    { method: "DELETE", token, workspaceId },
  );
}

export function resendInvitation(
  token: string,
  workspaceId: string,
  invitationId: string,
): Promise<Invitation> {
  return request<Invitation>(
    `/workspaces/${workspaceId}/invitations/${invitationId}/resend`,
    { method: "POST", body: JSON.stringify({}), token, workspaceId },
  );
}

// ---- Accept (authenticated, no workspace membership required) ----

export function acceptInvitation(
  token: string,
  input: AcceptInvitationInput,
): Promise<MemberOut> {
  return request<MemberOut>("/invitations/accept", {
    method: "POST",
    body: JSON.stringify(input),
    token,
  });
}
