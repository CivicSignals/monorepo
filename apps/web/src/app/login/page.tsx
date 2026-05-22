import type { Metadata } from "next";
import { LoginForm } from "@/components/auth/login-form";

export const metadata: Metadata = {
  title: "Sign in · CivicSignals",
  description: "Sign in to your CivicSignals account.",
};

export default function LoginPage() {
  return (
    <main className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-12">
      <LoginForm />
    </main>
  );
}
