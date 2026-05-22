// Saved-searches settings page — /settings/saved-searches (H1).
// Server-component shell; rendering is delegated to the SavedSearchManager
// client island so the auth/session hooks and TanStack Query work correctly.
import type { Metadata } from "next";
import { SavedSearchManager } from "@/components/searches/saved-search-manager";

export const metadata: Metadata = {
  title: "Saved searches | CivicSignals",
  description:
    "Create, rename, share, and delete saved feed searches for your workspace.",
};

export default function SavedSearchesSettingsPage() {
  return (
    <main className="container max-w-3xl py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">Saved searches</h1>
        <p className="mt-1 text-muted-foreground">
          Save a set of feed filters to re-run later, and share them with your
          workspace.
        </p>
      </div>
      <SavedSearchManager />
    </main>
  );
}
