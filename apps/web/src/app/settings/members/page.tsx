// Members settings page — /settings/members (B6).
// Server-component shell; rendering is delegated to the MembersSettings client
// island so the auth/session hooks and TanStack Query work correctly.
import type { Metadata } from "next";
import { MembersSettings } from "./members-settings";

export const metadata: Metadata = {
  title: "Members | CivicSignals",
  description:
    "Invite teammates and manage pending invitations for your workspace.",
};

export default function MembersSettingsPage() {
  return (
    <main className="container max-w-3xl py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">Team members</h1>
        <p className="mt-1 text-muted-foreground">
          Invite teammates to your workspace and manage pending invitations.
        </p>
      </div>
      <MembersSettings />
    </main>
  );
}
