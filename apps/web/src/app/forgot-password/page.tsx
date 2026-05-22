// B3: Forgot-password page — links to /api/v1/auth/password-reset/request.
import type { Metadata } from "next";
import { ForgotPasswordForm } from "@/components/auth/forgot-password-form";

export const metadata: Metadata = {
  title: "Forgot password · CivicSignals",
  description: "Request a password-reset link for your CivicSignals account.",
};

export default function ForgotPasswordPage() {
  return (
    <main className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-12">
      <ForgotPasswordForm />
    </main>
  );
}
