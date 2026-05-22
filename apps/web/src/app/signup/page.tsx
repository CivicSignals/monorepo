import type { Metadata } from "next";
import { SignupForm } from "@/components/auth/signup-form";

export const metadata: Metadata = {
  title: "Sign up · CivicSignals",
  description: "Create your CivicSignals account.",
};

// N6's pricing CTA links here (/signup?plan=...). The plan param is read by the
// form for later billing wiring (B-series); auth itself ignores it for now.
export default function SignupPage() {
  return (
    <main className="container flex min-h-[calc(100vh-3.5rem)] flex-col items-center justify-center py-12">
      <SignupForm />
    </main>
  );
}
