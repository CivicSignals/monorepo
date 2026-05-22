"use client";

import { useState } from "react";
import { TokenManager } from "@/components/api-tokens/token-manager";
import { useActiveWorkspace, useWorkspaces } from "@/hooks/use-workspaces";

// API-token settings island (B8).
//
// Lets the signed-in user manage their personal access tokens, and — when an
// active workspace is selected — that workspace's API tokens (admin-gated by the
// API; non-admins get a 403 surfaced inline). The active workspace comes from
// the same selection the header switcher drives (doc 08 §1.4).
type Tab = "personal" | "workspace";

export function TokensSettings() {
  const { data: workspaces } = useWorkspaces();
  const active = useActiveWorkspace(workspaces);
  const [tab, setTab] = useState<Tab>("personal");

  return (
    <div className="space-y-6">
      <div
        role="tablist"
        aria-label="Token type"
        className="flex gap-2 border-b"
      >
        <TabButton
          active={tab === "personal"}
          onClick={() => setTab("personal")}
        >
          Personal
        </TabButton>
        <TabButton
          active={tab === "workspace"}
          onClick={() => setTab("workspace")}
        >
          Workspace
        </TabButton>
      </div>

      {tab === "personal" ? (
        <TokenManager kind="personal" />
      ) : active ? (
        <TokenManager kind="workspace" workspaceId={active.id} />
      ) : (
        <p className="text-sm text-muted-foreground">
          Select or create a workspace first to manage its API tokens.
        </p>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={
        active
          ? "border-b-2 border-primary px-3 py-2 text-sm font-semibold"
          : "px-3 py-2 text-sm text-muted-foreground hover:text-foreground"
      }
    >
      {children}
    </button>
  );
}
