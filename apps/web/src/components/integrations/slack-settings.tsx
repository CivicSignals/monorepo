// Slack connection + channel-selection UI (L1; doc 08 §3.6).
//
// Admin-only settings island: connect Slack (OAuth redirect), then pick the
// notification channel from the bot's accessible channel list (public channels,
// conversations.list). TanStack Query owns all server state (doc 06 §2).
//
// Mirrors the Salesforce settings pattern from salesforce-settings.tsx (K2).
// A # TODO L2 marker is placed where the L2 message-send wiring will attach.
"use client";

import { useEffect, useState } from "react";
import {
  useConnectSlack,
  useConnections,
  useSelectedSlackChannel,
  useSelectSlackChannel,
  useSlackChannels,
} from "@/hooks/use-slack";
import { useDisconnect } from "@/hooks/use-salesforce";
import type { Connection } from "@/lib/slack-api";

export function SlackSettings({
  workspaceId,
}: {
  workspaceId: string | undefined;
}) {
  const connections = useConnections(workspaceId);
  const slackConnections = (connections.data ?? []).filter(
    (c) => c.provider === "slack" && c.status !== "revoked",
  );

  if (!workspaceId) {
    return (
      <p className="text-sm text-muted-foreground">
        Select or create a workspace first to manage Slack.
      </p>
    );
  }

  return (
    <div className="space-y-6" data-testid="slack-settings">
      <ConnectCard workspaceId={workspaceId} />
      {connections.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading connections…</p>
      ) : slackConnections.length === 0 ? (
        <p
          className="text-sm text-muted-foreground"
          data-testid="no-slack-connections"
        >
          No Slack connection yet. Connect one above to choose a notification
          channel.
        </p>
      ) : (
        slackConnections.map((conn) => (
          <ConnectionPanel
            key={conn.id}
            workspaceId={workspaceId}
            connection={conn}
          />
        ))
      )}
    </div>
  );
}

function ConnectCard({ workspaceId }: { workspaceId: string }) {
  const [name, setName] = useState("Slack workspace");
  const connect = useConnectSlack(workspaceId);

  function onConnect() {
    connect.mutate(name, {
      onSuccess: (created) => {
        if (created.redirect_url) {
          window.location.href = created.redirect_url;
        }
      },
    });
  }

  return (
    <div className="rounded-lg border p-4" data-testid="slack-connect-card">
      <h2 className="text-lg font-semibold">Connect Slack</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Authorize CivicSignals to post signal notifications to your Slack
        workspace.
      </p>
      <div className="mt-3 flex gap-2">
        <input
          aria-label="Connection name"
          className="flex-1 rounded-md border px-3 py-2 text-sm"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <button
          type="button"
          data-testid="connect-slack-btn"
          disabled={connect.isPending || name.trim().length === 0}
          onClick={onConnect}
          className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
        >
          {connect.isPending ? "Connecting…" : "Connect"}
        </button>
      </div>
      {connect.isError ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          Could not start the Slack connection. Check that the integration is
          configured.
        </p>
      ) : null}
    </div>
  );
}

function ConnectionPanel({
  workspaceId,
  connection,
}: {
  workspaceId: string;
  connection: Connection;
}) {
  const disconnect = useDisconnect(workspaceId);
  const channels = useSlackChannels(
    workspaceId,
    connection.id,
    connection.status === "healthy" || connection.status === "degraded",
  );
  const selected = useSelectedSlackChannel(workspaceId, connection.id);
  const selectMutation = useSelectSlackChannel(workspaceId, connection.id);

  const [pendingChannelId, setPendingChannelId] = useState<string>("");

  // Seed the dropdown with the already-selected channel once loaded.
  useEffect(() => {
    if (selected.data && !pendingChannelId) {
      setPendingChannelId(selected.data.channel_id);
    }
  }, [selected.data, pendingChannelId]);

  function onSaveChannel() {
    const ch = (channels.data ?? []).find((c) => c.id === pendingChannelId);
    if (!ch) return;
    selectMutation.mutate({ channelId: ch.id, channelName: ch.name });
  }

  return (
    <div
      className="rounded-lg border p-4"
      data-testid={`slack-connection-${connection.id}`}
    >
      <div className="flex items-start justify-between">
        <div>
          <h3 className="font-semibold">{connection.name}</h3>
          <p className="text-sm text-muted-foreground">
            Status:{" "}
            <span data-testid="slack-connection-status">{connection.status}</span>
          </p>
          {connection.provider_account?.team_name ? (
            <p className="text-sm text-muted-foreground">
              Workspace: {String(connection.provider_account.team_name)}
            </p>
          ) : null}
        </div>
        <button
          type="button"
          data-testid="slack-disconnect-btn"
          onClick={() => disconnect.mutate(connection.id)}
          className="rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
        >
          Disconnect
        </button>
      </div>

      {connection.status === "needs_reauth" ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          This connection needs to be reconnected before notifications can be
          sent.
        </p>
      ) : null}

      {connection.status === "pending_oauth" ? (
        <p className="mt-2 text-sm text-muted-foreground">
          Complete the Slack authorization to pick a notification channel.
        </p>
      ) : (
        <div className="mt-4">
          <label
            htmlFor={`slack-channel-${connection.id}`}
            className="block text-sm font-medium"
          >
            Notification channel
          </label>
          <p className="text-xs text-muted-foreground">
            CivicSignals will post signal notifications to this channel.
          </p>
          {channels.isLoading || selected.isLoading ? (
            <p className="mt-2 text-sm text-muted-foreground">
              Loading channels…
            </p>
          ) : channels.isError ? (
            <p role="alert" className="mt-2 text-sm text-destructive">
              Could not load channels (reconnect may be required).
            </p>
          ) : (
            <>
              <div className="mt-2 flex gap-2">
                <select
                  id={`slack-channel-${connection.id}`}
                  data-testid="slack-channel-select"
                  className="flex-1 rounded-md border px-3 py-2 text-sm"
                  value={pendingChannelId}
                  onChange={(e) => setPendingChannelId(e.target.value)}
                >
                  {pendingChannelId === "" ? (
                    <option value="">— select a channel —</option>
                  ) : null}
                  {(channels.data ?? []).map((ch) => (
                    <option key={ch.id} value={ch.id}>
                      #{ch.name}
                      {ch.is_member ? " (joined)" : ""}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  data-testid="save-slack-channel-btn"
                  disabled={
                    selectMutation.isPending || pendingChannelId.trim() === ""
                  }
                  onClick={onSaveChannel}
                  className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
                >
                  {selectMutation.isPending ? "Saving…" : "Save"}
                </button>
              </div>

              {selected.data ? (
                <p className="mt-1 text-xs text-muted-foreground">
                  Currently sending to{" "}
                  <span className="font-medium">
                    #{selected.data.channel_name}
                  </span>
                  .
                  {/* TODO L2: show last message sent timestamp once L2 lands. */}
                </p>
              ) : (
                <p className="mt-1 text-xs text-muted-foreground">
                  No channel selected yet.
                </p>
              )}

              {selectMutation.isSuccess ? (
                <p role="status" className="mt-1 text-sm text-green-600">
                  Channel saved.
                </p>
              ) : null}
              {selectMutation.isError ? (
                <p role="alert" className="mt-1 text-sm text-destructive">
                  Could not save the channel selection.
                </p>
              ) : null}
            </>
          )}
        </div>
      )}
    </div>
  );
}
