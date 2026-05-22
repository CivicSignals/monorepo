// Slack integration API client for the web app (L1).
//
// Talks to the FastAPI integrations endpoints under NEXT_PUBLIC_API_BASE_URL.
// TanStack Query owns the *server* state (see src/hooks/use-slack.ts); this
// module is the thin transport.
//
// Surface (doc 08 §3.6, L1): connect Slack via OAuth, list available channels,
// and persist the admin's chosen notification channel.  All endpoints are
// admin-gated + workspace-scoped by the API.

import { ProblemError, type Problem } from "@/lib/auth-api";
import type { Connection, ConnectionCreated } from "@/lib/salesforce-api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

export type { Connection, ConnectionCreated };

export interface SlackChannel {
  id: string;
  name: string;
  is_private: boolean;
  is_member: boolean;
}

export interface SlackChannelSelection {
  id: string;
  connection_id: string;
  channel_id: string;
  channel_name: string;
  created_at: string;
  updated_at: string;
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

// ---- Connections (shared with Salesforce API) ----

export function connectSlack(
  token: string,
  workspaceId: string,
  name: string,
): Promise<ConnectionCreated> {
  return request<ConnectionCreated>("/integrations/connections", {
    method: "POST",
    body: JSON.stringify({ provider: "slack", name }),
    token,
    workspaceId,
  });
}

// ---- Channel listing ----

export async function listSlackChannels(
  token: string,
  workspaceId: string,
  connectionId: string,
): Promise<SlackChannel[]> {
  const body = await request<{ data: SlackChannel[] }>(
    `/integrations/connections/${connectionId}/slack/channels`,
    { token, workspaceId },
  );
  return body.data;
}

// ---- Channel selection ----

export function getSelectedChannel(
  token: string,
  workspaceId: string,
  connectionId: string,
): Promise<SlackChannelSelection> {
  return request<SlackChannelSelection>(
    `/integrations/connections/${connectionId}/slack/channels/selected`,
    { token, workspaceId },
  );
}

export function selectChannel(
  token: string,
  workspaceId: string,
  connectionId: string,
  channelId: string,
  channelName: string,
): Promise<SlackChannelSelection> {
  return request<SlackChannelSelection>(
    `/integrations/connections/${connectionId}/slack/channels/select`,
    {
      method: "PUT",
      body: JSON.stringify({ channel_id: channelId, channel_name: channelName }),
      token,
      workspaceId,
    },
  );
}
