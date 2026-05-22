// API tokens settings page — /settings/tokens (B8).
// Server-component shell; rendering is delegated to the TokensSettings client
// island so the auth/session hooks and TanStack Query work correctly.
import type { Metadata } from "next";
import { TokensSettings } from "./tokens-settings";

export const metadata: Metadata = {
  title: "API Tokens | CivicSignals",
  description:
    "Create and revoke personal and workspace API tokens. The token secret is shown once at creation.",
};

export default function TokensSettingsPage() {
  return (
    <main className="container max-w-3xl py-8">
      <div className="mb-6">
        <h1 className="text-3xl font-bold tracking-tight">API tokens</h1>
        <p className="mt-1 text-muted-foreground">
          Create bearer tokens for the API and CLI. Each token&apos;s secret is
          shown once — copy it before you close the dialog.
        </p>
      </div>
      <TokensSettings />
    </main>
  );
}
