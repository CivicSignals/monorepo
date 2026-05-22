"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ProblemError } from "@/lib/auth-api";
import { useUnsubscribe } from "@/hooks/use-digests";

// One-click unsubscribe confirm page (H5) — /settings/notifications/unsubscribe.
//
// The digest email's footer "Unsubscribe" link lands here with ?token=<signed>.
// We require an explicit click to confirm before POSTing the token to the API, so
// a mail-client link prefetch or security scanner that follows the GET link can
// never silently unsubscribe the recipient (the destructive action is a POST). On
// success we name the digest that was turned off and link to the full preferences
// page. The native mail-client one-click button (RFC 8058 List-Unsubscribe-Post)
// targets the API POST endpoint directly and does not pass through this page.

export function DigestUnsubscribe() {
  const params = useSearchParams();
  const token = params.get("token");
  const unsubscribe = useUnsubscribe();

  if (!token) {
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="unsubscribe-missing-token"
      >
        <h1 className="text-2xl font-bold tracking-tight">Missing link</h1>
        <p className="text-sm text-muted-foreground">
          This page expects an unsubscribe link from a digest email.
        </p>
      </div>
    );
  }

  if (unsubscribe.isSuccess) {
    const name = unsubscribe.data.saved_search_name;
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="unsubscribe-success"
      >
        <h1 className="text-2xl font-bold tracking-tight">Unsubscribed</h1>
        <p className="text-sm text-muted-foreground">
          {name
            ? `You will no longer receive the "${name}" digest.`
            : "You will no longer receive this digest."}
        </p>
        <Link
          href="/settings/notifications"
          className="text-sm font-medium underline underline-offset-4"
        >
          Manage all notification preferences
        </Link>
      </div>
    );
  }

  if (unsubscribe.isError) {
    const detail =
      unsubscribe.error instanceof ProblemError
        ? unsubscribe.error.problem.detail
        : unsubscribe.error.message;
    return (
      <div
        className="w-full max-w-sm space-y-3 text-center"
        data-testid="unsubscribe-error"
      >
        <h1 className="text-2xl font-bold tracking-tight">
          Unsubscribe failed
        </h1>
        <p role="alert" className="text-sm text-destructive">
          {detail ?? "This unsubscribe link is invalid or has expired."}
        </p>
        <Link
          href="/settings/notifications"
          className="text-sm font-medium underline underline-offset-4"
        >
          Manage preferences instead
        </Link>
      </div>
    );
  }

  return (
    <div
      className="w-full max-w-sm space-y-4 text-center"
      data-testid="unsubscribe-confirm"
    >
      <h1 className="text-2xl font-bold tracking-tight">Unsubscribe</h1>
      <p className="text-sm text-muted-foreground">
        Stop receiving this saved-search email digest? You can re-enable it any
        time from your notification preferences.
      </p>
      <button
        type="button"
        onClick={() => unsubscribe.mutate(token)}
        disabled={unsubscribe.isPending}
        className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
      >
        {unsubscribe.isPending ? "Unsubscribing…" : "Confirm unsubscribe"}
      </button>
    </div>
  );
}
