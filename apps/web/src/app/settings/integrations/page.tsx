// Integrations settings page — /settings/integrations (K2).
// Server-component shell; rendering is delegated to the IntegrationsSettings
// client island (Suspense-wrapped because it reads search params from the OAuth
// callback) so auth/session hooks and TanStack Query work correctly.
import type { Metadata } from "next";
import { Suspense } from "react";
import { IntegrationsSettings } from "./integrations-settings";

export const metadata: Metadata = {
  title: "Integrations | CivicSignals",
  description:
    "Connect Salesforce, map signal fields to CRM fields, and push signals to your CRM.",
};

export default function IntegrationsSettingsPage() {
  return (
    <main className="container max-w-3xl py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">Integrations</h1>
        <p className="mt-1 text-muted-foreground">
          Connect Salesforce and map how signals become CRM records.
        </p>
      </div>
      <Suspense fallback={null}>
        <IntegrationsSettings />
      </Suspense>
    </main>
  );
}
