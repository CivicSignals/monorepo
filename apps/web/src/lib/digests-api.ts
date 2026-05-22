// Saved-search digest API client for the web app (H3).
//
// Talks to the FastAPI notifications digest endpoints under
// NEXT_PUBLIC_API_BASE_URL (e.g. http://localhost:8000/api/v1/notifications).
// TanStack Query owns the *server* state (see src/hooks/use-digests.ts); this
// module is the thin transport layer.
//
// A digest is per (saved search, current user) and workspace-scoped, so every
// call sends the X-Workspace-Id header (doc 08 §1.4, B5).

import { ProblemError, type Problem } from "@/lib/auth-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

// Mirrors notifications.digest.DigestFrequency.
export type DigestFrequency = "off" | "daily" | "weekly";

// Mirrors notifications.schemas.DigestSubscriptionOut.
export interface DigestSubscription {
  id: string;
  saved_search_id: string;
  workspace_id: string;
  user_id: string;
  frequency: DigestFrequency;
  send_hour: number;
  weekday: number;
  timezone: string;
  last_sent_at: string | null;
  created_at: string;
  updated_at: string;
}

// Mirrors notifications.schemas.DigestSubscriptionUpsert.
export interface DigestSubscriptionUpsert {
  frequency: DigestFrequency;
  send_hour?: number;
  weekday?: number;
  timezone?: string;
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

  const res = await fetch(`${API_BASE_URL}${path}`, { ...rest, headers: merged });
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

/**
 * The caller's digest schedule for a saved search, or null when none is set
 * (the API returns 404 for "no digest configured"; we map that to null so the
 * "off" default renders without surfacing an error).
 */
export async function getDigest(
  token: string,
  workspaceId: string,
  savedSearchId: string,
): Promise<DigestSubscription | null> {
  try {
    return await request<DigestSubscription>(
      `/notifications/digests/${encodeURIComponent(savedSearchId)}`,
      { token, workspaceId },
    );
  } catch (err) {
    if (err instanceof ProblemError && err.problem.status === 404) return null;
    throw err;
  }
}

/** Create or update the caller's digest schedule for a saved search. */
export function setDigest(
  token: string,
  workspaceId: string,
  savedSearchId: string,
  input: DigestSubscriptionUpsert,
): Promise<DigestSubscription> {
  return request<DigestSubscription>(
    `/notifications/digests/${encodeURIComponent(savedSearchId)}`,
    {
      method: "PUT",
      body: JSON.stringify(input),
      token,
      workspaceId,
    },
  );
}
