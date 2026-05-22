"use client";

// AcceptInvitePage — client island for /accept-invite (B6).
//
// Reads the `token` search-param. If the user is signed in it fires the
// accept mutation immediately (with a confirmation prompt); if not, it shows
// CTAs to log in or sign up, preserving the token in the redirect URL so
// this page re-tries after authentication.

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useSessionStore } from "@/store/session";
import { useAcceptInvitation } from "@/hooks/use-invitations";
import { ProblemError } from "@/lib/auth-api";

export function AcceptInvitePage() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const accessToken = useSessionStore((s) => s.accessToken);
  const isSignedIn = accessToken !== null;

  const accept = useAcceptInvitation();
  const [accepted, setAccepted] = useState(false);

  // If the user is already signed in and a token is present, offer to accept
  // immediately. We don't auto-fire without a user action (avoid accidental
  // double-accepts on page refreshes).
  const handleAccept = () => {
    if (!token) return;
    accept.mutate(token, {
      onSuccess: () => setAccepted(true),
    });
  };

  // If there's no token at all, show a generic error.
  if (!token) {
    return (
      <main className="container max-w-md py-16 text-center">
        <h1 className="text-2xl font-bold">Invalid invitation link</h1>
        <p className="mt-2 text-muted-foreground">
          This link is missing its invitation token. Please use the link from
          the invitation email.
        </p>
        <Link
          href="/"
          className="mt-6 inline-block text-sm text-primary underline"
        >
          Go to home
        </Link>
      </main>
    );
  }

  if (accepted) {
    return (
      <main className="container max-w-md py-16 text-center">
        <h1 className="text-2xl font-bold">You&apos;re in!</h1>
        <p className="mt-2 text-muted-foreground">
          You have joined the workspace successfully.
        </p>
        <Link
          href="/"
          className="mt-6 inline-block rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground"
        >
          Go to dashboard
        </Link>
      </main>
    );
  }

  if (!isSignedIn) {
    // Not signed in — prompt to log in or sign up first.
    const encodedToken = encodeURIComponent(token);
    return (
      <main className="container max-w-md py-16 space-y-6 text-center">
        <h1 className="text-2xl font-bold">Accept your invitation</h1>
        <p className="text-muted-foreground">
          Sign in or create an account to accept this invitation and join the
          workspace.
        </p>
        <div className="flex flex-col gap-3">
          <Link
            href={`/login?redirect=/accept-invite%3Ftoken%3D${encodedToken}`}
            className="rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:opacity-90"
          >
            Sign in
          </Link>
          <Link
            href={`/signup?redirect=/accept-invite%3Ftoken%3D${encodedToken}`}
            className="rounded border px-4 py-2 text-sm font-semibold hover:bg-muted"
          >
            Create account
          </Link>
        </div>
      </main>
    );
  }

  // Signed in — show the accept button.
  return (
    <main className="container max-w-md py-16 space-y-6 text-center">
      <h1 className="text-2xl font-bold">Accept your invitation</h1>
      <p className="text-muted-foreground">
        Click below to join the workspace. Your role will be applied
        automatically.
      </p>

      {accept.error && (
        <p className="rounded bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {accept.error instanceof ProblemError
            ? accept.error.problem.detail ?? accept.error.problem.title
            : "Failed to accept the invitation. The link may be expired or already used."}
        </p>
      )}

      <button
        type="button"
        onClick={handleAccept}
        disabled={accept.isPending}
        className="rounded bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground hover:opacity-90 disabled:opacity-60"
      >
        {accept.isPending ? "Joining…" : "Accept invitation"}
      </button>

      <p className="text-xs text-muted-foreground">
        Already a member?{" "}
        <Link href="/" className="underline">
          Go to dashboard
        </Link>
      </p>
    </main>
  );
}
