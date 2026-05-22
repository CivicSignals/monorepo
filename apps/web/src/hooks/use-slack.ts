// Slack integration hooks (L1) — TanStack Query owns server state (doc 06 §2).
//
// Drives the Slack connection + channel-selection UI: connect via OAuth,
// list available channels, get/set the notification channel.
// All endpoints are admin-gated + workspace-scoped by the API.

"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type ConnectionCreated,
  type SlackChannel,
  type SlackChannelSelection,
  connectSlack,
  getSelectedChannel,
  listSlackChannels,
  selectChannel,
} from "@/lib/slack-api";
import { useConnections } from "@/hooks/use-salesforce";
import { useSessionStore } from "@/store/session";

export { useConnections };

export const slackChannelsKey = (
  workspaceId: string | undefined,
  connectionId: string | undefined,
) =>
  [
    "integrations",
    "slack",
    "channels",
    workspaceId ?? "none",
    connectionId ?? "none",
  ] as const;

export const slackSelectedChannelKey = (
  workspaceId: string | undefined,
  connectionId: string | undefined,
) =>
  [
    "integrations",
    "slack",
    "selected-channel",
    workspaceId ?? "none",
    connectionId ?? "none",
  ] as const;

/** Start the Slack OAuth install flow; resolves with a redirect_url to the Slack consent page. */
export function useConnectSlack(workspaceId: string | undefined) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<ConnectionCreated, Error, string>({
    mutationFn: (name) => {
      if (!token || !workspaceId)
        return Promise.reject(new Error("no active workspace"));
      return connectSlack(token, workspaceId, name);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["integrations", "connections", workspaceId ?? "none"],
      });
    },
  });
}

/** List the public Slack channels available to the connected bot. */
export function useSlackChannels(
  workspaceId: string | undefined,
  connectionId: string | undefined,
  enabled = true,
) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<SlackChannel[]>({
    queryKey: slackChannelsKey(workspaceId, connectionId),
    queryFn: async () => {
      if (!token || !workspaceId || !connectionId) return [];
      return listSlackChannels(token, workspaceId, connectionId);
    },
    enabled: token !== null && !!workspaceId && !!connectionId && enabled,
  });
}

/** Get the currently selected Slack notification channel for a connection. */
export function useSelectedSlackChannel(
  workspaceId: string | undefined,
  connectionId: string | undefined,
) {
  const token = useSessionStore((s) => s.accessToken);
  return useQuery<SlackChannelSelection | null>({
    queryKey: slackSelectedChannelKey(workspaceId, connectionId),
    queryFn: async () => {
      if (!token || !workspaceId || !connectionId) return null;
      return getSelectedChannel(token, workspaceId, connectionId).catch(
        (err: unknown) => {
          // 404 means no channel selected yet — return null instead of throwing.
          if (
            err &&
            typeof err === "object" &&
            "status" in err &&
            (err as { status: number }).status === 404
          ) {
            return null;
          }
          throw err;
        },
      );
    },
    enabled: token !== null && !!workspaceId && !!connectionId,
  });
}

/** Persist the admin's chosen Slack notification channel. */
export function useSelectSlackChannel(
  workspaceId: string | undefined,
  connectionId: string | undefined,
) {
  const token = useSessionStore((s) => s.accessToken);
  const queryClient = useQueryClient();
  return useMutation<
    SlackChannelSelection,
    Error,
    { channelId: string; channelName: string }
  >({
    mutationFn: ({ channelId, channelName }) => {
      if (!token || !workspaceId || !connectionId)
        return Promise.reject(new Error("no active workspace"));
      return selectChannel(token, workspaceId, connectionId, channelId, channelName);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: slackSelectedChannelKey(workspaceId, connectionId),
      });
    },
  });
}
