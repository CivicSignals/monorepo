// Billing settings page — /settings/billing (N5).
// Server-component shell; rendering delegated to the BillingSettings client
// island so auth/session hooks and TanStack Query work correctly.
import type { Metadata } from "next";
import { BillingSettings } from "./billing-settings";

export const metadata: Metadata = {
  title: "Billing | CivicSignals",
  description:
    "Manage your workspace subscription, view usage, and access the Stripe billing portal.",
};

export default function BillingSettingsPage() {
  return (
    <main className="container max-w-3xl py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">Billing</h1>
        <p className="mt-1 text-muted-foreground">
          Manage your workspace plan, view usage, and access the billing portal.
        </p>
      </div>
      <BillingSettings />
    </main>
  );
}
