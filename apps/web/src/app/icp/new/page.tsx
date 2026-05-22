// /icp/new — Create a new ICP definition (F2).
// Same wizard used for onboarding, surfaced directly for returning users
// who want to create a second ICP or start fresh.

import type { Metadata } from "next";
import { IcpWizard } from "@/components/icp/icp-wizard";

export const metadata: Metadata = {
  title: "New ICP | CivicSignals",
  description: "Create a new ideal customer profile.",
};

export default function NewIcpPage() {
  return (
    <main className="container max-w-2xl py-10">
      <div className="mb-8 space-y-1">
        <h1 className="text-3xl font-bold tracking-tight">
          Create a new ICP
        </h1>
        <p className="text-muted-foreground">
          Define an ideal customer profile to target your signal feed.
        </p>
      </div>
      <IcpWizard />
    </main>
  );
}
