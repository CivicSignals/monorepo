"use client";

import { useSearchParams } from "next/navigation";
import { SalesforceSettings } from "@/components/integrations/salesforce-settings";
import { useActiveWorkspace, useWorkspaces } from "@/hooks/use-workspaces";

// Integrations settings island (K2). Renders the Salesforce connection +
// field-mapping UI for the active workspace. The OAuth callback redirects back
// here with ?integration=connected|error so we can surface the outcome.
export function IntegrationsSettings() {
  const { data: workspaces } = useWorkspaces();
  const active = useActiveWorkspace(workspaces);
  const params = useSearchParams();
  const outcome = params.get("integration");

  return (
    <div className="space-y-6">
      {outcome === "connected" ? (
        <div role="status" className="rounded-md border border-green-500 p-3 text-sm">
          Salesforce connected.
        </div>
      ) : null}
      {outcome === "error" ? (
        <div role="alert" className="rounded-md border border-destructive p-3 text-sm">
          The Salesforce connection was cancelled or failed. Try again.
        </div>
      ) : null}
      <SalesforceSettings workspaceId={active?.id} />
    </div>
  );
}
