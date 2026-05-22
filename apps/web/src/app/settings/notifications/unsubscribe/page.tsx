// One-click unsubscribe confirm page — /settings/notifications/unsubscribe (H5).
// The digest email footer links here with ?token=<signed>. The token is read on
// the client (useSearchParams), so the body is wrapped in Suspense. The confirm +
// POST happens in the DigestUnsubscribe client island.
import type { Metadata } from "next";
import { Suspense } from "react";
import { DigestUnsubscribe } from "@/components/notifications/digest-unsubscribe";

export const metadata: Metadata = {
  title: "Unsubscribe · CivicSignals",
  description: "Unsubscribe from a CivicSignals saved-search email digest.",
};

export default function DigestUnsubscribePage() {
  return (
    <main className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-12">
      <Suspense
        fallback={<p className="text-sm text-muted-foreground">Loading…</p>}
      >
        <DigestUnsubscribe />
      </Suspense>
    </main>
  );
}
