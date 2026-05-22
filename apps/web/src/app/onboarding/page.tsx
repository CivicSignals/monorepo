// Onboarding — ICP wizard entry point (F2).
// Reached automatically after signup / workspace creation (J1 → J2 in doc 04).
// Renders the multi-step ICP definition wizard.
// Server component shell; the wizard itself is a client island.

import type { Metadata } from "next";
import { IcpWizard } from "@/components/icp/icp-wizard";

export const metadata: Metadata = {
  title: "Set up your ICP | CivicSignals",
  description:
    "Define your ideal customer profile so CivicSignals can surface the most relevant public-sector signals for your team.",
};

export default function OnboardingPage() {
  return (
    <main className="container max-w-2xl py-10">
      <div className="mb-8 space-y-1">
        <h1 className="text-3xl font-bold tracking-tight">
          Set up your ideal customer profile
        </h1>
        <p className="text-muted-foreground">
          This takes about 10 minutes. Tell us who you&apos;re selling to,
          which signals matter, and how you want your feed filtered. You can
          change everything later in Settings.
        </p>
      </div>
      <IcpWizard />
    </main>
  );
}
