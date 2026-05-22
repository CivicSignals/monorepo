"use client";

import { useSearchParams } from "next/navigation";
import { HubspotSettings } from "@/components/integrations/hubspot-settings";
import { SalesforceSettings } from "@/components/integrations/salesforce-settings";
import { SlackSettings } from "@/components/integrations/slack-settings";
import { useActiveWorkspace, useWorkspaces } from "@/hooks/use-workspaces";

// Integrations settings island (K2 + K3 + L1). Renders the Salesforce and
// HubSpot connection + field-mapping UIs and the Slack notification channel
// selector for the active workspace. The OAuth callback redirects back here with
// ?integration=connected|error so we can surface the outcome.
export function IntegrationsSettings() {
  const { data: workspaces } = useWorkspaces();
  const active = useActiveWorkspace(workspaces);
  const params = useSearchParams();
  const outcome = params.get("integration");

  return (
    <div className="space-y-10">
      {outcome === "connected" ? (
        <div role="status" className="rounded-md border border-green-500 p-3 text-sm">
          Integration connected successfully.
        </div>
      ) : null}
      {outcome === "error" ? (
        <div role="alert" className="rounded-md border border-destructive p-3 text-sm">
          The integration connection was cancelled or failed. Try again.
        </div>
      ) : null}

      <section aria-labelledby="salesforce-heading">
        <h2 id="salesforce-heading" className="mb-4 text-xl font-semibold">
          Salesforce
        </h2>
        <SalesforceSettings workspaceId={active?.id} />
      </section>

      <section aria-labelledby="hubspot-heading">
        <h2 id="hubspot-heading" className="mb-4 text-xl font-semibold">
          HubSpot
        </h2>
        <HubspotSettings workspaceId={active?.id} />
      </section>

      <section aria-labelledby="slack-heading">
        <h2 id="slack-heading" className="mb-4 text-xl font-semibold">
          Slack
        </h2>
        <SlackSettings workspaceId={active?.id} />
      </section>
    </div>
  );
}
