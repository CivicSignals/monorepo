// B3: Reset-password page — links to /api/v1/auth/password-reset/confirm.
// The reset token arrives via ?token=<opaque> from the reset email.
// useSearchParams() in ResetPasswordForm requires a Suspense boundary.
import type { Metadata } from "next";
import { Suspense } from "react";
import { ResetPasswordForm } from "@/components/auth/reset-password-form";

export const metadata: Metadata = {
  title: "Reset password · CivicSignals",
  description: "Set a new password for your CivicSignals account.",
};

export default function ResetPasswordPage() {
  return (
    <main className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-12">
      <Suspense fallback={<p className="text-sm text-muted-foreground">Loading…</p>}>
        <ResetPasswordForm />
      </Suspense>
    </main>
  );
}
