import type { Metadata } from "next";
import { Suspense } from "react";
import { VerifyEmail } from "@/components/auth/verify-email";

export const metadata: Metadata = {
  title: "Verify email · CivicSignals",
  description: "Confirm your CivicSignals email address.",
};

// The verification link mailed by the API is /verify-email?token=... — the token
// is read on the client (useSearchParams), so the body is wrapped in Suspense.
export default function VerifyEmailPage() {
  return (
    <main className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-12">
      <Suspense
        fallback={<p className="text-sm text-muted-foreground">Loading…</p>}
      >
        <VerifyEmail />
      </Suspense>
    </main>
  );
}
