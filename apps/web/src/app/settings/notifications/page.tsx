// Notification preferences page — /settings/notifications (H5).
// Server-component shell; rendering is delegated to the NotificationPreferences
// client island so the auth/session hooks and TanStack Query work correctly.
// This is the target of the digest email's "Manage your digest preferences" link.
import type { Metadata } from "next";
import { NotificationPreferences } from "@/components/notifications/notification-preferences";

export const metadata: Metadata = {
  title: "Notification preferences | CivicSignals",
  description:
    "Manage your saved-search email digests — change the frequency or unsubscribe per saved search.",
};

export default function NotificationsSettingsPage() {
  return (
    <main className="container max-w-3xl py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">
          Notification preferences
        </h1>
        <p className="mt-1 text-muted-foreground">
          Manage your saved-search email digests. Change how often you receive
          each one, or unsubscribe — these settings are per saved search and only
          affect you.
        </p>
      </div>
      <NotificationPreferences />
    </main>
  );
}
